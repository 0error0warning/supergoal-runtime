"""Recorded dependency-transport repair; does not edit task/tests or disable TLS/GPG."""

APT_HTTPS_BOOTSTRAP = r'''
set -eu
for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources; do
    [ -f "$source" ] || continue
    if grep -q 'http://deb.debian.org' "$source"; then
        before=$(sha256sum "$source" | cut -d ' ' -f 1)
        sed -i 's#http://deb.debian.org#https://deb.debian.org#g' "$source"
        after=$(sha256sum "$source" | cut -d ' ' -f 1)
        printf '%s\t%s\t%s\n' "$source" "$before" "$after"
    fi
done
'''


def parse_bootstrap(stdout):
    rows = []
    for line in (stdout or "").splitlines():
        path, before, after = line.split("\t")
        rows.append({"path": path, "before_sha256": before, "after_sha256": after,
                     "change": "HTTPS transport only; package signatures unchanged"})
    return rows
