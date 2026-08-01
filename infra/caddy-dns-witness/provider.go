// Package witness provides Caddy's DNS-01 solver without giving either VPS a
// Cloudflare credential. The provider can only ask the cluster witness to add
// or remove the exact ACME TXT record for the configured application hostname.
package witness

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/caddyserver/caddy/v2"
	"github.com/caddyserver/caddy/v2/caddyconfig/caddyfile"
	"github.com/libdns/libdns"
)

const defaultTokenFile = "/run/secrets/ha_node_token"

var identifier = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)

// Provider is deliberately configured from the same non-secret HA environment
// and Docker secret used by the lease agent. No Cloudflare token reaches Caddy.
type Provider struct {
	WitnessURL string `json:"witness_url,omitempty"`
	ClusterID string `json:"cluster_id,omitempty"`
	NodeID string `json:"node_id,omitempty"`
	TokenFile string `json:"token_file,omitempty"`

	token string
	client *http.Client
}

func init() { caddy.RegisterModule(Provider{}) }

func (Provider) CaddyModule() caddy.ModuleInfo {
	return caddy.ModuleInfo{
		ID: "dns.providers.mpopt_witness",
		New: func() caddy.Module { return new(Provider) },
	}
}

func (p *Provider) Provision(_ caddy.Context) error {
	replacer := caddy.NewReplacer()
	if p.WitnessURL == "" { p.WitnessURL = os.Getenv("HA_WITNESS_URL") }
	if p.ClusterID == "" { p.ClusterID = os.Getenv("HA_CLUSTER_ID") }
	if p.NodeID == "" { p.NodeID = os.Getenv("HA_NODE_ID") }
	if p.TokenFile == "" { p.TokenFile = defaultTokenFile }
	p.WitnessURL = strings.TrimRight(replacer.ReplaceAll(p.WitnessURL, ""), "/")
	u, err := url.Parse(p.WitnessURL)
	if err != nil || u.Scheme != "https" || u.Host == "" || u.User != nil {
		return fmt.Errorf("HA witness URL must be an HTTPS origin")
	}
	if !identifier.MatchString(p.ClusterID) || !identifier.MatchString(p.NodeID) {
		return fmt.Errorf("invalid HA cluster or node identifier")
	}
	token, err := os.ReadFile(p.TokenFile)
	if err != nil { return fmt.Errorf("read HA node credential: %w", err) }
	p.token = strings.TrimSpace(string(token))
	if len(p.token) < 32 || len(p.token) > 256 {
		return fmt.Errorf("invalid HA node credential")
	}
	p.client = &http.Client{Timeout: 15 * time.Second}
	return nil
}

func (p *Provider) UnmarshalCaddyfile(d *caddyfile.Dispenser) error {
	d.Next()
	if d.NextArg() { return d.ArgErr() }
	if d.NextBlock(0) { return d.Err("mpopt_witness does not accept inline credentials") }
	return nil
}

func (p *Provider) AppendRecords(
	ctx context.Context, zone string, records []libdns.Record,
) ([]libdns.Record, error) {
	for _, record := range records {
		rr := record.RR()
		if rr.Type != "TXT" { return nil, fmt.Errorf("only TXT records are supported") }
		name := strings.TrimSuffix(libdns.AbsoluteName(rr.Name, zone), ".")
		if err := p.call(ctx, "acme-present", name, rr.Data); err != nil { return nil, err }
	}
	return records, nil
}

func (p *Provider) DeleteRecords(
	ctx context.Context, zone string, records []libdns.Record,
) ([]libdns.Record, error) {
	for _, record := range records {
		rr := record.RR()
		if rr.Type != "TXT" { return nil, fmt.Errorf("only TXT records are supported") }
		name := strings.TrimSuffix(libdns.AbsoluteName(rr.Name, zone), ".")
		if err := p.call(ctx, "acme-cleanup", name, rr.Data); err != nil { return nil, err }
	}
	return records, nil
}

func (p *Provider) call(ctx context.Context, action, name, value string) error {
	body, err := json.Marshal(map[string]string{
		"node_id": p.NodeID, "record_name": name, "record_value": value,
	})
	if err != nil { return err }
	endpoint := fmt.Sprintf("%s/v1/clusters/%s/%s", p.WitnessURL, p.ClusterID, action)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(body))
	if err != nil { return err }
	request.Header.Set("Authorization", "Bearer "+p.token)
	request.Header.Set("Content-Type", "application/json")
	response, err := p.client.Do(request)
	if err != nil { return fmt.Errorf("HA witness DNS request failed: %w", err) }
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("HA witness DNS request returned HTTP %d", response.StatusCode)
	}
	return nil
}

var (
	_ caddy.Provisioner = (*Provider)(nil)
	_ caddyfile.Unmarshaler = (*Provider)(nil)
	_ libdns.RecordAppender = (*Provider)(nil)
	_ libdns.RecordDeleter = (*Provider)(nil)
)
