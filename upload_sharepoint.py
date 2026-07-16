"""Mirror the outputs/ folder into a SharePoint document library.

Uploads every file in outputs/ (chart JPG, curve CSV, run metadata) to a
SharePoint site via the Microsoft Graph API, using app-only (client
credentials) authentication so it runs unattended in CI.

Configuration comes entirely from environment variables:

  Credentials (store as GitHub Actions *secrets*):
    AZURE_TENANT_ID       Entra ID tenant (directory) ID
    AZURE_CLIENT_ID       app registration (client) ID
    AZURE_CLIENT_SECRET   client secret value

  Destination (store as GitHub Actions *variables* — not sensitive):
    SHAREPOINT_HOSTNAME   e.g. contoso.sharepoint.com
    SHAREPOINT_SITE_PATH  server-relative site path, e.g. /sites/EnergyTeam
    SHAREPOINT_DRIVE      document library name (optional; default: site default)
    SHAREPOINT_FOLDER     target folder within the library, e.g. gas-chart

Exits nonzero with a clear message on any failure so the CI job fails visibly.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import msal
import requests

GRAPH = "https://graph.microsoft.com/v1.0"
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"

MAX_ATTEMPTS = 3
BACKOFF_BASE_SECONDS = 1.0
TIMEOUT = 60


class UploadError(RuntimeError):
    pass


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise UploadError(f"missing required environment variable: {name}")
    return value


def get_token(tenant: str, client_id: str, secret: str) -> str:
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        client_credential=secret,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise UploadError(
            "failed to acquire Graph token: "
            f"{result.get('error')}: {result.get('error_description')}"
        )
    return result["access_token"]


def graph_request(method: str, url: str, token: str, **kwargs) -> requests.Response:
    """Graph call with retry/backoff on 429 and 5xx."""
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = requests.request(
                method, url, headers=headers, timeout=TIMEOUT, **kwargs
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise UploadError(
                    f"transient Graph error {resp.status_code}: {resp.text[:200]}"
                )
            return resp
        except (requests.RequestException, UploadError) as exc:
            last_exc = exc
            if attempt < MAX_ATTEMPTS - 1:
                delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(
                    f"  [warn] {method} {url.split(GRAPH)[-1]}: attempt "
                    f"{attempt + 1}/{MAX_ATTEMPTS} failed ({exc}); "
                    f"retrying in {delay:.0f}s",
                    file=sys.stderr,
                )
                time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def resolve_site_id(token: str, hostname: str, site_path: str) -> str:
    site_path = "/" + site_path.strip("/")
    resp = graph_request("GET", f"{GRAPH}/sites/{hostname}:{site_path}", token)
    if resp.status_code != 200:
        raise UploadError(
            f"could not resolve site {hostname}:{site_path} "
            f"({resp.status_code}: {resp.text[:200]})"
        )
    return resp.json()["id"]


def resolve_drive_id(token: str, site_id: str, drive_name: str | None) -> str:
    if not drive_name:
        resp = graph_request("GET", f"{GRAPH}/sites/{site_id}/drive", token)
        if resp.status_code != 200:
            raise UploadError(
                f"could not resolve default drive ({resp.status_code}: "
                f"{resp.text[:200]})"
            )
        return resp.json()["id"]

    resp = graph_request("GET", f"{GRAPH}/sites/{site_id}/drives", token)
    if resp.status_code != 200:
        raise UploadError(
            f"could not list drives ({resp.status_code}: {resp.text[:200]})"
        )
    for drive in resp.json().get("value", []):
        if drive.get("name") == drive_name:
            return drive["id"]
    raise UploadError(f"document library not found: {drive_name!r}")


def get_root_id(token: str, drive_id: str) -> str:
    resp = graph_request("GET", f"{GRAPH}/drives/{drive_id}/root", token)
    if resp.status_code != 200:
        raise UploadError(f"could not get drive root ({resp.status_code})")
    return resp.json()["id"]


def _child_id(token: str, drive_id: str, parent_id: str, name: str) -> str | None:
    """Return the id of an existing child by name, or None if absent."""
    url = f"{GRAPH}/drives/{drive_id}/items/{parent_id}:/{quote(name, safe='')}"
    resp = graph_request("GET", url, token)
    if resp.status_code == 200:
        return resp.json()["id"]
    if resp.status_code == 404:
        return None
    raise UploadError(
        f"could not look up {name!r} ({resp.status_code}: {resp.text[:200]})"
    )


def ensure_folder(token: str, drive_id: str, folder: str) -> str:
    """Get-or-create the (possibly nested) target folder; return its item id.

    Non-destructive: existing folders are reused as-is (their contents are
    never touched); only missing segments are created.
    """
    parent_id = get_root_id(token, drive_id)
    for segment in [s for s in folder.strip("/").split("/") if s]:
        existing = _child_id(token, drive_id, parent_id, segment)
        if existing is not None:
            parent_id = existing
            continue
        # conflictBehavior "fail" so a race never clobbers an existing folder.
        resp = graph_request(
            "POST",
            f"{GRAPH}/drives/{drive_id}/items/{parent_id}/children",
            token,
            json={
                "name": segment,
                "folder": {},
                "@microsoft.graph.conflictBehavior": "fail",
            },
        )
        if resp.status_code == 409:
            existing = _child_id(token, drive_id, parent_id, segment)
            if existing is None:
                raise UploadError(f"folder {segment!r} conflict but not found")
            parent_id = existing
            continue
        if resp.status_code not in (200, 201):
            raise UploadError(
                f"could not create folder {segment!r} "
                f"({resp.status_code}: {resp.text[:200]})"
            )
        parent_id = resp.json()["id"]
    return parent_id


def upload_file(token: str, drive_id: str, parent_id: str, path: Path) -> None:
    url = f"{GRAPH}/drives/{drive_id}/items/{parent_id}:/{path.name}:/content"
    resp = graph_request(
        "PUT", url, token,
        headers={"Content-Type": "application/octet-stream"},
        data=path.read_bytes(),
    )
    if resp.status_code not in (200, 201):
        raise UploadError(
            f"upload failed for {path.name} ({resp.status_code}: "
            f"{resp.text[:200]})"
        )
    web_url = resp.json().get("webUrl", "")
    print(f"  uploaded {path.name} -> {web_url}")


def main() -> int:
    try:
        tenant = _require_env("AZURE_TENANT_ID")
        client_id = _require_env("AZURE_CLIENT_ID")
        secret = _require_env("AZURE_CLIENT_SECRET")
        hostname = _require_env("SHAREPOINT_HOSTNAME")
        site_path = _require_env("SHAREPOINT_SITE_PATH")
        drive_name = os.environ.get("SHAREPOINT_DRIVE", "").strip() or None
        folder = os.environ.get("SHAREPOINT_FOLDER", "").strip()

        files = sorted(p for p in OUTPUT_DIR.iterdir() if p.is_file())
        if not files:
            raise UploadError(f"no files to upload in {OUTPUT_DIR}")

        print("Authenticating to Microsoft Graph (app-only) ...")
        token = get_token(tenant, client_id, secret)

        site_id = resolve_site_id(token, hostname, site_path)
        drive_id = resolve_drive_id(token, site_id, drive_name)
        parent_id = (
            ensure_folder(token, drive_id, folder)
            if folder else get_root_id(token, drive_id)
        )

        dest = f"{drive_name or 'default library'}/{folder}".rstrip("/")
        print(f"Mirroring {len(files)} file(s) to {hostname}:{site_path} [{dest}]")
        for path in files:
            upload_file(token, drive_id, parent_id, path)
    except UploadError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("SharePoint mirror complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
