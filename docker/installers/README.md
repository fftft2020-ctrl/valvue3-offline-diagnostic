Place an official Linux amd64 WPS installer here as `wps-office.deb` only if you
are entitled to install it. Do not commit it. Set WPS_DEB_SHA256 from
`sha256sum docker/installers/wps-office.deb` and RUNTIME_TARGET=with-wps in .env.
The runtime target works without this installer but cannot deliver formal PDFs.
