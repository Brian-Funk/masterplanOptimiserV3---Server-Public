"""Regression checks for the production dependency and image security policy."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


class ContainerSecurityPolicyTests(unittest.TestCase):
    def test_release_tree_excludes_retired_commissioning_tools(self) -> None:
        retired = (
            "deploy/ha/ha_verification.py",
            "deploy/ha/ha_observer.py",
            "docs/active-ha-verification.md",
            "docs/ha-verification.md",
            "docs/recovery-drill-report.md",
            "backend/generate_vapid_keys.py",
            "backend/rotate_vapid_keys.py",
        )
        for relative in retired:
            self.assertFalse((ROOT / relative).exists(), relative)

        management = (ROOT / "deploy/management/ha.sh").read_text(encoding="utf-8")
        documentation = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "docs").glob("*.md")
        )
        self.assertNotIn("ha_verification.py", management)
        self.assertNotIn("active-ha-verification.md", documentation)

    def test_backend_uses_audited_dependency_line(self) -> None:
        requirements = (ROOT / "backend/requirements.txt").read_text(encoding="utf-8")

        self.assertIn("fastapi==0.139.2", requirements)
        self.assertIn("python-dotenv==1.2.2", requirements)
        self.assertIn("httpx2==2.9.1", requirements)
        self.assertNotIn("python-dotenv==1.0.1", requirements)
        self.assertNotIn("httpx==0.27.2", requirements)

    def test_backend_image_checks_dependencies_and_removes_build_tools(self) -> None:
        dockerfile = (ROOT / "infra/Dockerfile").read_text(encoding="utf-8")

        self.assertIn("FROM python:3.14-alpine", dockerfile)
        self.assertIn("apk upgrade --no-cache", dockerfile)
        self.assertIn("apk add --no-cache font-dejavu", dockerfile)
        self.assertIn("PIP_ROOT_USER_ACTION=ignore", dockerfile)
        self.assertIn("pip check", dockerfile)
        self.assertIn("pip uninstall --yes setuptools wheel", dockerfile)

    def test_runtime_images_are_hardened_without_replacing_entrypoints(self) -> None:
        caddy = (ROOT / "infra/Dockerfile.caddy").read_text(encoding="utf-8")
        postgres = (ROOT / "infra/Dockerfile.postgres").read_text(encoding="utf-8")
        tools = (ROOT / "infra/Dockerfile.tools").read_text(encoding="utf-8")
        compose = (ROOT / "infra/docker-compose.yml").read_text(encoding="utf-8")

        self.assertIn("ARG GO_VERSION=1.26.5", caddy)
        self.assertIn("ARG GRPC_VERSION=v1.82.1", caddy)
        self.assertIn("ARG X_TEXT_VERSION=v0.39.0", caddy)
        self.assertIn("xcaddy build ${CADDY_VERSION}", caddy)
        self.assertIn(
            "--replace google.golang.org/grpc@v1.81.0="
            "google.golang.org/grpc@${GRPC_VERSION}",
            caddy,
        )
        self.assertIn(
            "--replace golang.org/x/text="
            "golang.org/x/text@${X_TEXT_VERSION}",
            caddy,
        )
        self.assertIn('awk -v grpc_version="${GRPC_VERSION}"', caddy)
        self.assertIn('-v text_version="${X_TEXT_VERSION}"', caddy)
        self.assertIn(
            '$1 == "=>" && $2 == "google.golang.org/grpc" '
            '&& $3 == grpc_version { grpc_found=1 }',
            caddy,
        )
        self.assertIn(
            '$1 == "=>" && $2 == "golang.org/x/text" '
            '&& $3 == text_version { text_found=1 }',
            caddy,
        )
        self.assertIn(
            'END { exit !(grpc_found && text_found) }',
            caddy,
        )
        self.assertNotIn(
            'grep -F "=> google.golang.org/grpc ${GRPC_VERSION}"', caddy
        )
        self.assertIn("RUN apk upgrade --no-cache", caddy)
        self.assertIn("ARG GO_VERSION=1.26.5", postgres)
        self.assertIn("go install github.com/tianon/gosu@${GOSU_VERSION}", postgres)
        self.assertIn("FROM postgres:16-alpine", postgres)
        self.assertIn("FROM node:25-alpine", tools)
        self.assertIn("ARG WRANGLER_VERSION=4.115.0", tools)
        self.assertIn("RUN apk upgrade --no-cache", tools)
        self.assertNotIn("ENTRYPOINT", postgres)
        self.assertIn('npm install --global "wrangler@${WRANGLER_VERSION}"', tools)
        self.assertIn("/usr/local/lib/node_modules/npm", tools)
        self.assertIn("/usr/local/lib/node_modules/corepack", tools)
        self.assertIn("/opt/yarn-v*", tools)
        self.assertIn("test ! -e /usr/local/lib/node_modules/npm", tools)
        self.assertLess(
            tools.index("rm -rf"),
            tools.index("wrangler --version"),
        )
        self.assertIn("dockerfile: infra/Dockerfile.caddy", compose)
        self.assertIn("dockerfile: infra/Dockerfile.postgres", compose)

    def test_deploy_refreshes_images_sequentially_with_buildkit(self) -> None:
        deploy = (ROOT / "deploy/deploy.sh").read_text(encoding="utf-8")
        common = (ROOT / "deploy/management/common.sh").read_text(encoding="utf-8")

        self.assertIn("export DOCKER_BUILDKIT=1", deploy)
        self.assertIn("for service in db caddy backend", deploy)
        self.assertIn('build --pull "$service"', deploy)
        self.assertIn("mp_build_frontend_container", deploy)
        self.assertIn("node:22-alpine", common)

    def test_ci_audits_dependencies_with_one_bounded_non_applicable_exception(self) -> None:
        workflow = (ROOT / ".github/workflows/server-ci.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("python -m pip_audit -r requirements.lock.txt", workflow)
        self.assertIn("--ignore-vuln CVE-2026-69247", workflow)
        self.assertIn("does not call those APIs", workflow)
        self.assertEqual(workflow.count("--ignore-vuln"), 1)
        self.assertIn(
            "pip install --constraint requirements.lock.txt -r requirements.txt",
            workflow,
        )
        self.assertIn("aquasec/trivy:0.72.0", workflow)
        self.assertIn("pull_with_retry()", workflow)
        self.assertIn('for attempt in 1 2 3; do', workflow)
        for image in (
            "python:3.14-alpine",
            "golang:1.26.5-alpine",
            "caddy:2-alpine",
            "postgres:16-alpine",
            "aquasec/trivy:0.72.0",
        ):
            self.assertIn(image, workflow)
        self.assertNotIn("docker build --pull", workflow)
        self.assertIn("--severity HIGH,CRITICAL", workflow)
        self.assertNotIn("--ignore-unfixed", workflow)
        self.assertIn("--exit-code 1", workflow)
        self.assertIn("mp-opt-caddy:ci caddy fmt --diff", workflow)
        self.assertIn("npm ci --no-audit", workflow)
        self.assertIn("npm audit --omit=dev --audit-level=high", workflow)

        package = (ROOT / "web/package.json").read_text(encoding="utf-8")
        self.assertIn('"next": "16.2.11"', package)
        self.assertIn('"eslint-config-next": "16.2.11"', package)
        self.assertIn('"sharp": "0.35.0"', package)


if __name__ == "__main__":
    unittest.main()
