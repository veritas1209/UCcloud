"""Verify the real public Funnel route, bypassing tailnet DNS rewriting."""
import http.client,ipaddress,json,socket,ssl,urllib.request
HOST='comsv-03.tail5819db.ts.net'

def public_addresses():
    # DNS-over-HTTPS avoids local tailnet DNS rewriting, including DNS interception.
    with urllib.request.urlopen('https://dns.google/resolve?name='+HOST+'&type=A',timeout=15) as response:
        value=json.load(response)
    assert value.get('Status')==0
    addresses=[x['data'] for x in value.get('Answer',[]) if x.get('type')==1 and ipaddress.ip_address(x['data']).is_global]
    assert addresses,'Public Funnel DNS not available; do not count tailnet-only access as public access'
    return addresses

def public_get(path,addresses,limit=256000):
    assert path.startswith('/') and '\r' not in path and '\n' not in path
    for address in addresses:
        connection=http.client.HTTPSConnection(HOST,timeout=15)
        try:
            raw=socket.create_connection((address,443),timeout=15)
            connection.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=HOST)
            connection.request('GET',path,headers={'Host':HOST})
            response=connection.getresponse();body=response.read(limit)
            return response.status,body
        except (OSError,ssl.SSLError,http.client.HTTPException):continue
        finally:connection.close()
    raise RuntimeError('Public Funnel relay did not respond')
