#!/bin/bash
set -euo pipefail

[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ ]] || {
    echo "Usage: $0 CONTROL_COMMIT_SHA (run as root on Atlas)" >&2; exit 1;
}
revision="$1"
[[ $(id -u) == 0 && $(hostname -f) == atlas.home.arpa ]] || {
    echo 'Run as root on atlas.home.arpa.' >&2; exit 1;
}
github=git@github.com:Enucatl/puppet-control-repo.git
checkout=/opt/docker/puppet-control-repo
bare=/opt/git/openvox/control.git
control_git=(runuser -u user_l -- git -C "$checkout")
bare_git=(runuser -u user_l -- git --git-dir="$bare")
[[ $("${control_git[@]}" remote get-url origin) == "$github" &&
   -z $("${control_git[@]}" status --porcelain) ]] || {
    echo 'Atlas requires a clean control checkout with its expected GitHub origin.' >&2; exit 1;
}

known_hosts="$checkout/.git/github-known-hosts"
cat > "$known_hosts" <<'KEY'
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
KEY
chown user_l:docker "$known_hosts"
chmod 0644 "$known_hosts"
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$known_hosts"
"${control_git[@]}" config --local core.sshCommand "$GIT_SSH_COMMAND"
if [[ $("${control_git[@]}" rev-parse --is-shallow-repository) == true ]]; then
    "${control_git[@]}" fetch --unshallow origin
fi
"${control_git[@]}" fetch origin \
    +refs/heads/dev:refs/remotes/origin/dev \
    +refs/heads/production:refs/remotes/origin/production
"${control_git[@]}" merge-base --is-ancestor "$revision" origin/dev || {
    echo 'The requested commit must already be published on GitHub dev.' >&2; exit 1;
}
for ref in HEAD refs/heads/production refs/remotes/origin/production; do
    if "${control_git[@]}" show-ref --verify --quiet "$ref" || [[ $ref == HEAD ]]; then
        "${control_git[@]}" merge-base --is-ancestor "$ref" "$revision" || {
            echo "Refusing to replace commits from $ref." >&2; exit 1;
        }
    fi
done
if [[ -e $bare ]]; then
    [[ $("${bare_git[@]}" rev-parse --is-bare-repository) == true ]] || {
        echo 'Atlas control repository path is not a bare repository.' >&2; exit 1;
    }
    if "${bare_git[@]}" show-ref --verify --quiet refs/heads/production; then
        "${control_git[@]}" fetch "$bare" refs/heads/production
        "${control_git[@]}" merge-base --is-ancestor FETCH_HEAD "$revision" || {
            echo 'Refusing to replace commits in the Atlas bare repository.' >&2; exit 1;
        }
    fi
else
    install -d -o user_l -g docker -m 2775 /opt/git/openvox "$bare"
    # An empty template prevents legacy host hooks from being installed.
    runuser -u user_l -- git init --bare --shared=group --initial-branch=production --template= "$bare"
fi
[[ -z $("${bare_git[@]}" config --get core.hooksPath || true) ]] || {
    echo 'Publishing setup requires an unhooked Atlas bare repository.' >&2; exit 1;
}
for hook in "$bare"/hooks/*; do
    [[ ! -x $hook || $hook == *.sample ]] || {
        echo 'Publishing setup refuses an active bare-repository hook.' >&2; exit 1;
    }
done
# This receiver contains public GitHub code; remapped container users may read it.
chmod 2775 /opt/git/openvox "$bare"
"${bare_git[@]}" config core.sharedRepository 0664
chmod -R o+rX,o-w "$bare"

if "${control_git[@]}" show-ref --verify --quiet refs/heads/production; then
    "${control_git[@]}" switch production
    "${control_git[@]}" merge --ff-only "$revision"
else
    "${control_git[@]}" switch --create production "$revision"
fi
"${control_git[@]}" config --local --replace-all remote.origin.pushurl "$github"
"${control_git[@]}" config --local --add remote.origin.pushurl "$bare"
"${control_git[@]}" config --local --replace-all remote.origin.push refs/heads/production:refs/heads/production
"${control_git[@]}" config --local remote.pushDefault origin
"${control_git[@]}" config --local branch.production.pushRemote origin
"${control_git[@]}" push origin
"${control_git[@]}" branch --set-upstream-to=origin/production production
[[ $("${control_git[@]}" ls-remote "$github" refs/heads/production | cut -f1) == "$revision" &&
   $("${bare_git[@]}" rev-parse refs/heads/production) == "$revision" ]] || {
    echo 'Production targets differ; rerun the same publication before continuing.' >&2; exit 1;
}
echo "Atlas production published to GitHub and $bare: $revision"
