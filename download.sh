#!/bin/env bash

# This documents the commands used to download the archive
cd /data/source
# Download major complex sites with HTTrack, which has better crawling
httrack --update -c 3 -T 90 -R 9 \
'-*worldvista.org/*/view' '-*worldvista.org/*/download' \
'-*ihs.gov/rpms/applications/ftp/*' \
'https://opensourcevista.net/NancysVistAServer/' \
'https://www.va.gov/vdl/' \
'https://www.ihs.gov/rpms/' \
'https://www.ihs.gov/sites/RPMS/SRCB/' \
'https://www.ihs.gov/cis/' \
'https://www.ihs.gov/sites/cis/themes/' \
'http://worldvista.org' \
'https://hardhats.org' \
'https://www.va.gov/vdl/documents/Monograph/Monograph/vista_monograph_0723_r.docx'
# Download simple static sites that use Apache indexes
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait --reject-regex '\?C=|\?O=' https://code.worldvista.org/
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait --reject-regex '\?C=|\?O=' https://resources.worldvista.org/
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait --reject-regex '\?C=|\?O=' https://journal.worldvista.org/
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait https://education.worldvista.org/
wget2 --mirror --page-requisites --no-parent --robots=off --wait=1 --random-wait --reject-regex '\?C=|\?O=' https://foia-vista.worldvista.org
# Download IHS RPMS FTP (dynamic file browser - requires custom crawler)
python download-ihs-ftp.py --resume
# Download VistApedia
python download-vistapedia.py
# Download the GitHub repositories
ORG=WorldVistA
mkdir /data/source/$ORG
cd /data/source/$ORG
gh repo list $ORG --limit 1000 --json name --jq '.[].name' | while read -r REPO; do
    gh repo clone "$ORG/$REPO" "$REPO" -- --depth 1
done
# Add a worktree for the FOIA branch, since that has some unique code
git worktree add ../VistA-M-foia foia
# Store everyting in Google Cloud Storage
gsutil rsync -x ".*\.git.*" -r . gs://vista-rpms-archive/source
