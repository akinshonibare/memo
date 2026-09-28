#!/usr/bin/env sh
# Push the provider API keys from the local .env to both Railway services.
#
# Reads only the three named keys, never prints their values, and skips any that
# are empty. Requires `railway login` and `railway link` to the memo project.
set -eu

cd "$(dirname "$0")/.."
[ -f .env ] || { echo ".env not found" >&2; exit 1; }

set_from_env() {
	key="$1"
	value="$(grep -E "^${key}=" .env | head -n1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
	if [ -z "$value" ]; then
		echo "skip  $key (empty in .env)"
		return
	fi
	for service in api worker; do
		railway variables --service "$service" --set "${key}=${value}" --skip-deploys >/dev/null
		echo "set   $key on $service"
	done
}

set_from_env ANTHROPIC_API_KEY
set_from_env VOYAGE_API_KEY
set_from_env DEEPGRAM_API_KEY

echo "Redeploying both services so the new variables take effect..."
railway redeploy --service api -y >/dev/null
railway redeploy --service worker -y >/dev/null
echo "done"
