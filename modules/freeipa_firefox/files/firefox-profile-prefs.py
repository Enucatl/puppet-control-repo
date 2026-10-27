#!/usr/bin/python3
"""Apply Puppet preferences to existing Firefox profiles as their owning user."""

from pathlib import Path
import stat
import sys
import tempfile


BEGIN = "// BEGIN Puppet Firefox preferences"
END = "// END Puppet Firefox preferences"


def synchronize(home: Path, template: Path, *, check: bool = False) -> bool:
    """Update a managed user.js block while preserving other profile settings."""
    block = BEGIN + "\n" + template.read_text().rstrip() + "\n" + END
    current = True
    for root in (
        home / ".mozilla/firefox",
        home / "snap/firefox/common/.mozilla/firefox",
        home / ".config/mozilla/firefox",
    ):
        for preferences in sorted(root.glob("*/prefs.js")):
            target = preferences.with_name("user.js")
            existing = target.read_bytes().decode() if target.exists() else ""
            if BEGIN in existing or END in existing:
                if (
                    existing.count(BEGIN) != 1
                    or existing.count(END) != 1
                    or existing.index(BEGIN) > existing.index(END)
                ):
                    raise ValueError(f"Malformed Puppet preference block: {target}")
                start = existing.index(BEGIN)
                stop = existing.index(END) + len(END)
                content = existing[:start] + block + existing[stop:]
            else:
                separator = "\n" if existing and not existing.endswith("\n") else ""
                content = existing + separator + block + "\n"
            if content == existing:
                continue
            current = False
            if check:
                continue
            mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as output:
                temporary = Path(output.name)
                try:
                    output.write(content.encode())
                    output.flush()
                    temporary.chmod(mode)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
    return current


if __name__ == "__main__":
    check = sys.argv[1:] == ["--check"]
    current = synchronize(
        Path.home(), Path("/etc/firefox/policies/user.js"), check=check
    )
    sys.exit(int(check and not current))
