# Intermediate certificates

PEM files here are added to the trust bundle the fetcher uses, on top of
`certifi`'s roots. Everything in this directory is a **public CA intermediate**
— no secrets, nothing site-specific, safe to commit.

## Why this directory exists

Some Nepali government sites serve only their leaf certificate and omit the
intermediate that links it to a trusted root. `hr.parliament.gov.np` is one:

    $ openssl s_client -connect hr.parliament.gov.np:443 -servername hr.parliament.gov.np
    0 s:CN = *.parliament.gov.np          <- the entire chain
    Verify return code: 21 (unable to verify the first certificate)

Browsers and curl hide this. When a chain is incomplete they read the leaf's
Authority Information Access extension and download the missing intermediate
themselves. Python's `ssl` module does not implement AIA fetching, so httpx
fails where the browser succeeds:

    [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed:
    unable to get local issuer certificate

This is a misconfiguration on the server, not on your machine, and we cannot
fix it from here. Supplying the intermediate ourselves completes the chain and
lets normal verification proceed.

**This is not a way to skip verification.** Certificates are still validated in
full — expiry, hostname, signature, chain to a certifi root. `verify=False`
would disable all of that and is never the right answer for a public site.

## Adding a certificate

When a source fails with `unable to get local issuer certificate`, confirm the
server really is sending a short chain, then fetch the intermediate the leaf
names:

    # 1. Confirm: does the chain stop at the leaf?
    echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null \
      | grep -E "^ *[0-9] s:|Verify return code"

    # 2. Find the intermediate's URL in the leaf's AIA extension
    echo | openssl s_client -connect <host>:443 -servername <host> 2>/dev/null \
      | openssl x509 -noout -text | grep -A2 "Authority Information Access"

    # 3. Download it and convert DER -> PEM
    curl -s <that CA Issuers URL> -o inter.der
    openssl x509 -inform DER -in inter.der -out certs/<descriptive-name>.pem

    # 4. Prove it completes the chain BEFORE committing
    openssl verify -CAfile "$(python -c 'import certifi;print(certifi.where())')" \
      -untrusted certs/<descriptive-name>.pem leaf.pem

Step 4 must print `leaf.pem: OK`. If it does not, the intermediate is wrong or
the root genuinely is not trusted — do not work around that by disabling
verification.

Name the file after the certificate's subject, not the site: one intermediate
usually serves many hosts.

## Expiry

| file | subject | expires |
|---|---|---|
| `sectigo-public-server-authentication-ca-dv-r36.pem` | Sectigo Public Server Authentication CA DV R36 | 2036-03-21 |

`tests/test_fetcher_tls.py` fails once any certificate here is within 30 days
of expiring, so this does not become a silent outage years from now.
