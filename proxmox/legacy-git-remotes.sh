#!/bin/bash
set -euo pipefail

[[ $# == 1 ]] || { echo "Usage: $0 LEGACY_CHECKOUT_PATH" >&2; exit 1; }
checkout="$1"
github_url=git@github.com:Enucatl/puppet-control-repo.git
legacy_url=user_l@docker.home.arpa:/opt/git/puppet-control-repo.git
[[ $(git -C "$checkout" rev-parse --is-inside-work-tree) == true ]] || {
    echo 'Legacy checkout must be a Git worktree.' >&2; exit 1;
}
origin=$(git -C "$checkout" config --local --get-all remote.origin.url)
[[ $origin == "$github_url" || $origin == "$legacy_url" ]] || {
    echo 'Unexpected legacy origin; refusing to change its remotes.' >&2; exit 1;
}

ssh -o BatchMode=yes user_l@docker.home.arpa 'bash -se' <<'REMOTE'
set -euo pipefail
bare=/opt/git/puppet-control-repo.git
[[ $(git --git-dir="$bare" rev-parse --is-bare-repository) == true ]] || {
    echo 'Legacy receiver must be the expected bare repository.' >&2; exit 1;
}
remotes=$(git --git-dir="$bare" remote)
if grep -Fxq origin <<<"$remotes"; then
    origin=$(git --git-dir="$bare" config --local --get-all remote.origin.url)
    case "$origin" in
        https://github.com/Enucatl/puppet-control-repo.git|git@github.com:Enucatl/puppet-control-repo.git) ;;
        *) echo 'Unexpected legacy bare origin; refusing to remove it.' >&2; exit 1 ;;
    esac
    # Remove only routing metadata; git remote remove would also delete tracking refs.
    git --git-dir="$bare" config --local --remove-section remote.origin
fi
REMOTE

git -C "$checkout" config --local --replace-all remote.origin.url "$legacy_url"
git -C "$checkout" config --local --replace-all remote.origin.pushurl "$legacy_url"
git -C "$checkout" config --local --replace-all remote.origin.push refs/heads/production:refs/heads/production
git -C "$checkout" config --local --replace-all remote.pushDefault origin
git -C "$checkout" config --local --replace-all branch.production.pushRemote origin
echo 'Legacy Git fetch and production push now use only the old Docker bare repository.'
