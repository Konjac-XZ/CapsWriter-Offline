#!/usr/bin/env python3
import os, sys, json, mimetypes, requests, re, urllib.parse

API_BASE = "https://api.replicate.com/v1"

def auth_header():
    token = os.environ.get("REPLICATE_API_TOKEN") or os.environ.get("REPLICATE_TOKEN")
    if not token:
        sys.exit("Error: Set REPLICATE_API_TOKEN in your environment.")
    return {"Authorization": f"Token {token}"}

def upload_file(file_path, override_filename=None, override_content_type=None, metadata=None):
    filename = override_filename or os.path.basename(file_path)
    content_type = override_content_type or mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    with open(file_path, "rb") as fh:
        files = {
            # Field name must be "content" (matches your curl). We control the server-visible
            # filename and Content-Type via this tuple.
            "content": (filename, fh, content_type),
        }
        if metadata is not None:
            files["metadata"] = ("metadata.json", json.dumps(metadata), "application/json")

        r = requests.post(f"{API_BASE}/files", headers=auth_header(), files=files, timeout=60)
    r.raise_for_status()
    return r.json()

def get_file_info(file_id: str):
    r = requests.get(f"{API_BASE}/files/{file_id}", headers=auth_header(), timeout=60)
    r.raise_for_status()
    return r.json()

def _collect_urls(obj):
    """Return list of (key, url) pairs found anywhere in a JSON-like object."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                found.extend(_collect_urls(v))
            elif isinstance(v, str) and v.startswith("http"):
                found.append((k, v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_collect_urls(item))
    return found

def pick_download_url(info: dict):
    """
    Heuristics to find a URL to download from in the file record.
    Prefers keys like 'download_url', 'serving_url', or plain 'url'.
    """
    candidates = _collect_urls(info)
    if not candidates:
        return None
    # prefer download-y looking keys
    for k, v in candidates:
        kl = k.lower()
        if "download" in kl or kl in {"serving_url", "url"}:
            return v
    # fallback to first url-looking value
    return candidates[0][1]

def download_to_disk(url: str, dest_dir="downloads", suggested_basename="download.bin"):
    os.makedirs(dest_dir, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        # try filename from Content-Disposition
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
        if m:
            final_name = m.group(1)
        else:
            # fall back to URL path or suggested name
            path = urllib.parse.urlparse(r.url).path
            final_name = os.path.basename(path) or suggested_basename

        out_path = os.path.join(dest_dir, final_name)
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)

        return out_path, r.headers.get("content-type"), cd

def run_case(label, file_path, override_filename=None, override_content_type=None):
    print(f"\n=== {label} ===")
    resp = upload_file(
        file_path,
        override_filename=override_filename,
        override_content_type=override_content_type,
        metadata={"customer_reference_id": 123},
    )
    print("Upload response JSON:")
    print(json.dumps(resp, indent=2))

    file_id = resp.get("id")
    if not file_id:
        print("No 'id' in upload response; cannot fetch file record.")
        return

    info = get_file_info(file_id)
    print("\nFile record JSON:")
    print(json.dumps(info, indent=2))

    url = pick_download_url(info)
    if not url:
        print("\nNo obvious download URL found in the file record.")
        return

    save_as_hint = override_filename or os.path.basename(file_path)
    path, ctype, cd = download_to_disk(url, suggested_basename=save_as_hint)
    print(f"\nDownloaded to: {path}")
    print(f"Response Content-Type: {ctype}")
    print(f"Response Content-Disposition: {cd or '(none)'}")
    print("Note the saved filename/extension and the headers above.")

def main():
    # Default to your Windows test path; allow override via CLI arg
    file_path = r"D:\test.mp3"
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    print(f"Using test file: {file_path}")

    # Case A: upload with original filename/type
    run_case("Case A: original name/type", file_path)

    # Case B: same bytes, but pretend it's a .zip via multipart filename + content-type
    run_case("Case B: overridden name/type",
             file_path,
             override_filename="example.zip",
             override_content_type="application/zip")

if __name__ == "__main__":
    main()