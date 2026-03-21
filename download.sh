#!/bin/env bash

# This documents the commands used to download the archive
cd /data/source
httrack --update -c 3 -T 90 -R 9 \
'-*worldvista.org/*/view' '-*worldvista.org/*/download' \
'https://opensourcevista.net/NancysVistAServer/' \
'https://www.va.gov/vdl/' \
'https://www.ihs.gov/rpms/' \
'https://www.ihs.gov/sites/RPMS/SRCB/' \
'http://worldvista.org' \
'https://hardhats.org' \
'https://www.va.gov/vdl/documents/Monograph/Monograph/vista_monograph_0723_r.docx' \
'https://vistapedia.com/' \
'https://journal.worldvista.org/' \
'https://education.worldvista.org/'
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait --reject-regex '\?C=|\?O=' https://code.worldvista.org/
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait --reject-regex '\?C=|\?O=' https://foia-vista.worldvista.org
ORG=WorldVistA
mkdir /data/source/$ORG
cd /data/source/$ORG
gh repo list $ORG --limit 1000 --json name --jq '.[].name' | while read -r REPO; do
    gh repo clone "$ORG/$REPO" "$REPO" -- --depth 1
done
git worktree add ../VistA-M-foia foia
gsutil rsync -x ".*\.git.*" -r . gs://vista-rpms-archive/source
