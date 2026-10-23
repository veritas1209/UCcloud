"""Read-only availability check for every allowlisted Docker service version."""
import json
from urllib.request import Request, urlopen
from backend.software import SOFTWARE

names = dict(nginx="nginx", apache="httpd", postgres="postgres", mysql="mysql", redis="redis", mongo="mongo", maria="mariadb", tomcat="tomcat")
for item in SOFTWARE:
    if item["id"] not in names:
        continue
    name = names[item["id"]]
    with urlopen(f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/{name}:pull", timeout=30) as response:
        token = json.load(response)["token"]
    for version in item["versions"]:
        if item['id'] == 'tomcat':
            version += '-jdk21-temurin'
        request = Request(f"https://registry-1.docker.io/v2/library/{name}/manifests/{version}", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json",
        })
        with urlopen(request, timeout=30) as response:
            manifest = json.load(response)
        assert any(row.get("platform", {}).get("os") == "linux" and row.get("platform", {}).get("architecture") == "amd64"
                   for row in manifest["manifests"]), f"No amd64 image: {name}:{version}"
        print(f"Available linux/amd64: {name}:{version}", flush=True)
