#!/bin/bash
# Fetch the act 2 model ahead of time: RedHatAI/Qwen3-Coder-Next-NVFP4 from Hugging Face, 47.6 GB, at one
# pinned revision, into /data/models. Hours on a slow uplink, so it is made to be left alone: run it in a
# detached tmux, stop it whenever, run it again and it carries on where it was.
#
#   tmux new -d -s model ~/flywheel-setup/60-model-fetch.sh      start it and walk away
#   tmux attach -t model                                         look in (Ctrl-b d to leave it running)
#   RATE=6M ~/flywheel-setup/60-model-fetch.sh                   the same, capped at 6 MB/s
#   ~/flywheel-setup/60-model-fetch.sh check                     no download: what is there, what is missing
#   MODEL_ROOT=<dir> ONLY='\.json$' ...                          somewhere else, or only the files matching a regex
#
# No root, no Python, no token (the repository is public): curl with resume, then every file is checked -
# the large ones against the sha256 Hugging Face publishes, the small ones against their git blob id.
# Once, before the first run:   sudo install -d -o "$USER" -g "$USER" /data/models
#
# This project was developed with assistance from AI tools.
set -uo pipefail

repo=RedHatAI/Qwen3-Coder-Next-NVFP4
rev=27a8f16f463b9a13c91c332c40cf93e09717347e        # a branch can move, a commit cannot
root=${MODEL_ROOT:-/data/models}
dest=$root/$repo
die() { echo "${0##*/}: $*" >&2; exit 1; }

[[ $EUID -ne 0 ]]   || die "run this as an ordinary user: a download that takes hours should not sit on a sudo session"
[[ -w $root ]]      || die "$root is missing or not yours. Once:  sudo install -d -o $USER -g $USER $root"
for c in curl jq sha256sum sha1sum; do command -v "$c" >/dev/null || die "$c is missing"; done
mkdir -p "$dest" "$root/log"
exec > >(tee -a "$root/log/$(basename "$0" .sh).log") 2>&1

list=$dest/.files.json
curl -fsSL --retry 10 --retry-delay 10 "https://huggingface.co/api/models/$repo/revision/$rev?blobs=true" |
    jq -c --arg only "${ONLY:-.}" '[.siblings[] | select(.rfilename | test($only)) | {f: .rfilename, size, sha256: .lfs.sha256, blob: .blobId}]' > "$list.new" && mv "$list.new" "$list"
[[ -s $list ]] || die "could not read the file list from Hugging Face"
total=$(jq '[.[].size] | add' "$list")

# sha256 for the LFS files; for the rest the git blob id, which is sha1("blob <size>\0" + content)
good() {  # file size sha256 blob
    [[ -f $1 && $(stat -c %s "$1") -eq $2 ]] || return 1
    if [[ $3 != null ]]; then
        [[ $(sha256sum "$1" | cut -d' ' -f1) == "$3" ]]
    else
        [[ $( { printf 'blob %s\0' "$2"; cat "$1"; } | sha1sum | cut -d' ' -f1) == "$4" ]]
    fi
}

have=0 missing=0
date -u
echo "$repo @ ${rev:0:12}: $(jq length "$list") files, $((total / 1000000000)) GB -> $dest   (free: $(df -h --output=avail "$root" | tail -1 | tr -d ' '))"
while IFS=$'\t' read -r f size sha blob; do
    out=$dest/$f
    if [[ -f $out.ok ]] || good "$out" "$size" "$sha" "$blob"; then
        touch "$out.ok"; have=$((have + size)); continue
    fi
    if [[ ${1:-} == check ]]; then
        echo "missing or incomplete: $f ($(( $(stat -c %s "$out.part" 2>/dev/null || echo 0) / 1000000 )) of $((size / 1000000)) MB)"
        missing=$((missing + 1)); continue
    fi
    mkdir -p "$(dirname "$out")"
    echo "== $f  ($((size / 1000000)) MB)   $(date -u +%T)   done so far: $((have / 1000000000)) of $((total / 1000000000)) GB"
    # -C - resumes a partial file; the retries ride out a relay that drops now and then
    until curl -fL -C - --retry 30 --retry-delay 20 --retry-all-errors --connect-timeout 30 \
               ${RATE:+--limit-rate "$RATE"} -o "$out.part" \
               "https://huggingface.co/$repo/resolve/$rev/$f"; do
        rc=$?
        # 33: the server would not resume; 416 inside -f shows as 22 when the part is already complete
        if [[ $rc -eq 22 || $rc -eq 33 ]] && [[ $(stat -c %s "$out.part" 2>/dev/null || echo 0) -ge $size ]]; then break; fi
        tries=$((${tries:-0} + 1))
        if [[ $tries -ge 8 ]]; then echo "giving up on $f for this run (curl: $rc)"; break; fi
        echo "curl stopped with $rc, trying again in a minute"; sleep 60
    done
    tries=0
    if good "$out.part" "$size" "$sha" "$blob"; then
        mv "$out.part" "$out"; touch "$out.ok"; have=$((have + size))
    else
        echo "checksum or size mismatch on $f: deleting the partial file, it is fetched again on the next run"
        rm -f "$out.part"; missing=$((missing + 1))
    fi
done < <(jq -r '.[] | [.f, .size, (.sha256 // "null"), .blob] | @tsv' "$list")   # "null" spelled out: an empty tsv field would shift the columns

date -u
if [[ $missing -eq 0 ]]; then
    echo "complete and verified: $(jq length "$list") files, $(du -sh --exclude='*.ok' "$dest" | cut -f1) in $dest"
else
    die "$missing file(s) still missing - run this again"
fi
