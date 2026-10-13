"""Build public client ZIPs from reviewed source; no credentials or runtime state."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = {"Folder-Locator": "folder-locator.zip", "Website-Client-Checker": "website-client-checker.zip"}


def build():
    for folder, filename in PACKAGES.items():
        source = ROOT / "clients" / folder
        files = sorted(p for p in source.iterdir() if p.is_file() and p.suffix.lower() in {".js", ".json", ".ps1", ".cmd", ".txt"})
        if not files:
            raise FileNotFoundError(f"Client source missing: {source}")
        destination = ROOT / "web" / "static" / filename
        with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
            for path in files:
                info = ZipInfo(folder + "/" + path.name, date_time=(2026, 10, 13, 0, 0, 0))
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        print(f"Built {filename}: {len(files)} source files")


if __name__ == "__main__":
    build()
