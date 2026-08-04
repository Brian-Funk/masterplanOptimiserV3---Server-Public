{$DOMAIN:localhost} {
	# Certificates are issued directly by Caddy. The custom provider asks the
	# witness to create only this hostname's ACME TXT challenge; the Cloudflare
	# DNS token never reaches either VPS.
	tls {
		dns mpopt_witness
		resolvers 1.1.1.1 1.0.0.1
	}

	request_body {
		max_size 5MB
	}

	header {
		Strict-Transport-Security "max-age=63072000; includeSubDomains; preload"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
		Cross-Origin-Opener-Policy "same-origin"
		Cross-Origin-Resource-Policy "same-origin"
		X-Permitted-Cross-Domain-Policies "none"
		Permissions-Policy "camera=(), microphone=(), geolocation=()"
		import /etc/caddy/runtime/frontend-csp.caddy
		-Server
	}

	handle /api/* {
		reverse_proxy backend:8000
	}

	# The browser-local recovery-key generator is delivered only to an
	# authenticated root session. Its page performs an additional fresh-passkey
	# check before enabling key generation.
	@recoveryKey path /recovery-key /recovery-key/ /recovery-key.html
	forward_auth @recoveryKey backend:8000 {
		uri /api/v1/auth/root-access
	}

	handle /health {
		reverse_proxy backend:8000
	}

	handle /ha/ready {
		reverse_proxy backend:8000
	}

	handle /ha/status {
		reverse_proxy backend:8000
	}

	# Force browsers and intermediary caches to revalidate the service worker.
	# The worker controls application-shell updates, so caching this response for
	# hours can strand an installed desktop client on an obsolete HA state flow.
	@serviceWorker path /sw.js
	header @serviceWorker Cache-Control "no-cache, no-store, must-revalidate"
	@authenticationShell path /login /login/ /login.html /bootstrap /bootstrap/ /bootstrap.html
	header @authenticationShell Cache-Control "no-cache, no-store, must-revalidate"

	handle {
		root * /srv/static
		try_files {path} {path}/index.html {path}.html /index.html
		file_server
	}
}
