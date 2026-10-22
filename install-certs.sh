#!/usr/bin/env bash
#
# install-certs.sh — build the CA bundle the eClass server needs.
#
# thales.cs.unipi.gr serves its leaf certificate WITHOUT the HARICA
# intermediate that issued it, so Python/requests and curl fail with
# "unable to get local issuer certificate" (browsers work because they fetch
# the missing intermediate automatically; requests does not).
#
# This fetches that intermediate straight from the URL named in the server
# certificate itself (its AIA "CA Issuers" field), then combines it with the
# trusted root bundle into certs/eclass-ca-bundle.pem — which the bot picks up
# automatically (see General_Items/connection_const.py). TLS verification
# stays fully ON; we're only supplying the intermediate the server omits.
#
# The AIA URL is plain HTTP, so the download is only used if it is issued by
# one of the trusted roots, and the bundle only replaces the current one once
# the server's chain verifies against it. If the server certificate ever lacks
# that URL, an intermediate saved by hand as certs/intermediate.pem is used
# instead, with the same checks.
#
# Run once after cloning (and again if the certificate chain ever changes):
#   bash install-certs.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="thales.cs.unipi.gr"
CERT_DIR="$PROJECT_DIR/certs"
INT_PEM="$CERT_DIR/intermediate.pem"
OUT="$CERT_DIR/eclass-ca-bundle.pem"
mkdir -p "$CERT_DIR"

# The new intermediate and bundle are built next to the real ones (*.pem, so
# git-ignored) and only moved into place once everything checks out.
cleanup() { rm -f "$LEAF" "$INT_RAW" "$NEW_INT" "$NEW_OUT"; }
LEAF="$(mktemp)"; INT_RAW="$(mktemp)"
NEW_INT="$(mktemp --suffix=.pem "$CERT_DIR/.new-XXXXXX")"
NEW_OUT="$(mktemp --suffix=.pem "$CERT_DIR/.new-XXXXXX")"
trap cleanup EXIT

echo "Reading the server certificate from $HOST ..."
echo | openssl s_client -connect "$HOST:443" -servername "$HOST" 2>/dev/null \
    | openssl x509 > "$LEAF"

# Pull the "CA Issuers" URL (points at the issuing intermediate) from the
# certificate's Authority Information Access extension.
# `|| true`: with pipefail a grep that finds nothing would otherwise end the
# script here, silently, before the explanation below.
AIA_URL="$(openssl x509 -in "$LEAF" -noout -text \
    | grep -i 'CA Issuers' | head -n1 | sed -E 's/.*URI://; s/[[:space:]]//g' || true)"

# SOURCE names where the intermediate came from, for the error messages below.
if [ -n "$AIA_URL" ]; then
    echo "Intermediate certificate URL: $AIA_URL"
    curl -fsSL "$AIA_URL" -o "$INT_RAW"
    SOURCE="the downloaded file"
elif [ -f "$INT_PEM" ]; then
    # No URL to fetch from: use the intermediate saved by hand (see below). It
    # goes through the same trust and chain checks as a downloaded one.
    echo "No 'CA Issuers' URL in the server certificate; using $INT_PEM."
    cp "$INT_PEM" "$INT_RAW"
    SOURCE="$INT_PEM"
else
    echo "ERROR: no 'CA Issuers' URL in the server certificate." >&2
    echo "Download the 'HARICA Institutional TLS RSA 2' intermediate manually" >&2
    echo "from https://repo.harica.gr and save it (PEM) as:" >&2
    echo "  $INT_PEM" >&2
    echo "then re-run this script." >&2
    exit 1
fi

# The AIA file is usually DER-encoded; convert to PEM (fall back if already PEM).
if openssl x509 -inform DER -in "$INT_RAW" -out "$NEW_INT" 2>/dev/null; then
    :
elif openssl x509 -inform PEM -in "$INT_RAW" -out "$NEW_INT" 2>/dev/null; then
    :
else
    echo "ERROR: $SOURCE is not a certificate I can parse." >&2
    exit 1
fi
echo "Intermediate certificate ->"
openssl x509 -in "$NEW_INT" -noout -subject -issuer | sed 's/^/  /'

# Locate a trusted root bundle: prefer the venv's certifi (that's what the bot
# uses via requests), then common system locations.
ROOTS=""
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    ROOTS="$("$PROJECT_DIR/venv/bin/python" -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
fi
if [ -z "$ROOTS" ]; then
    for c in /etc/ssl/certs/ca-certificates.crt /etc/pki/tls/certs/ca-bundle.crt; do
        [ -f "$c" ] && ROOTS="$c" && break
    done
fi
if [ -z "$ROOTS" ]; then
    echo "ERROR: could not locate a trusted root CA bundle." >&2
    exit 1
fi
echo "Trusted roots: $ROOTS"

# Everything in the bundle is trusted, so the download (fetched over plain
# HTTP) must itself be issued by one of those roots: anything else, such as a
# self-signed certificate swapped in on the way, would become a trusted CA.
if ! openssl verify -CAfile "$ROOTS" "$NEW_INT" > /dev/null 2>&1; then
    echo "ERROR: the certificate in $SOURCE is not issued by a trusted root CA," >&2
    echo "so it was not used. The current bundle (if any) is unchanged." >&2
    exit 1
fi

# Combined bundle = public roots + the missing HARICA intermediate.
cat "$ROOTS" "$NEW_INT" > "$NEW_OUT"

echo "Verifying the chain against the new bundle ..."
# Capture first, then grep: piping straight into `grep -q` lets grep exit
# early, and under `set -o pipefail` the resulting SIGPIPE in s_client would
# report a false failure.
VERIFY_OUT="$(echo | openssl s_client -connect "$HOST:443" -servername "$HOST" -CAfile "$NEW_OUT" 2>/dev/null || true)"
if ! printf '%s\n' "$VERIFY_OUT" | grep -q "Verify return code: 0"; then
    echo "WARNING: verification still fails — inspect the certificate manually." >&2
    echo "The current bundle (if any) is unchanged." >&2
    exit 1
fi

# mktemp files are 0600; keep the usual mode so the bundle stays readable.
chmod 644 "$NEW_INT" "$NEW_OUT"
mv -f "$NEW_INT" "$INT_PEM"
mv -f "$NEW_OUT" "$OUT"
echo "Saved intermediate -> $INT_PEM"
echo "Wrote combined CA bundle -> $OUT"
echo "SUCCESS: the certificate chain now verifies. The bot will use this bundle."
