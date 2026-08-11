#!/bin/sh
set -eu

manifest=$1
destination_dir=$2
model_id=${WHISPER_MODEL_ID:?WHISPER_MODEL_ID is required}
model=$(jq -er --arg id "$model_id" '.models[$id] | select(.id == $id)' "$manifest")
url=$(printf '%s' "$model" | jq -er '.url')
sha256=$(printf '%s' "$model" | jq -er '.sha256 | select(test("^[a-f0-9]{64}$"))')
mkdir -p "$destination_dir"
destination="$destination_dir/$model_id"
curl --fail --location --retry 3 --output "$destination" "$url"
printf '%s  %s\n' "$sha256" "$destination" | sha256sum --check --status
