#!/bin/sh
# Imports the credentials and the three workflows into n8n and publishes them.
#
#   docker compose exec n8n sh /home/node/setup/setup.sh
#   docker compose restart n8n
#
# The OpenAI key comes from OPENAI_API_KEY (.env). It is written to a temporary file only for the import and
# deleted right after; inside n8n it is stored encrypted with N8N_ENCRYPTION_KEY. Running the script again is safe:
# credentials and workflows are overwritten by their fixed IDs.
set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
N8N_CMD=${N8N_CMD:-n8n}
: "${OPENAI_API_KEY:?OPENAI_API_KEY is missing - add it to .env and run: docker compose up -d}"

# On the first start n8n creates its database (migrations, ~1 min). Importing at the same time breaks the
# migration, so wait until the running n8n reports ready. WAIT_FOR_N8N=0 skips this (n8n not started yet).
if [ "${WAIT_FOR_N8N:-1}" = "1" ]; then
  printf "Waiting for n8n to be ready "
  i=0
  until node -e 'fetch("http://localhost:5678/healthz/readiness").then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))'; do
    i=$((i + 1))
    [ "$i" -ge 90 ] && { echo; echo "n8n is not ready after 3 minutes - check: docker compose logs n8n"; exit 1; }
    printf "."
    sleep 2
  done
  echo " ok"
fi

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT INT TERM

node -e '
const fs = require("fs");
const creds = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));
for (const c of creds) {
  if (c.type === "openAiApi") {
    c.data.apiKey = process.env.OPENAI_API_KEY;
    c.data.url = process.env.OPENAI_BASE_URL || "https://api.openai.com/v1";
  }
  if (c.type === "smtp") {
    c.data.host = process.env.SMTP_HOST || c.data.host;
    c.data.port = Number(process.env.SMTP_PORT || c.data.port);
  }
}
fs.writeFileSync(process.argv[2], JSON.stringify(creds));
' "$DIR/credentials.template.json" "$TMP"

$N8N_CMD import:credentials --input="$TMP"
$N8N_CMD import:workflow --separate --input="$DIR/workflows"
for id in kpiWeeklyReport1 kpiChatAssist001 kpiErrorNotify01; do
  $N8N_CMD publish:workflow --id="$id"
done

echo
echo "Done. Restart n8n so the published workflows are active:  docker compose restart n8n"
