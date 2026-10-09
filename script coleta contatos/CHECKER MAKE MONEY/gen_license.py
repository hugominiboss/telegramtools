import hmac
import base64
import hashlib

# mesma chave usada no verificador
key = b"minhachave32bytes1234567890abcd"

body = """product=mailchecker4
hwid=123456789abc
start=1710000000
hours=48"""

digest = hmac.new(key, body.encode("utf-8"), hashlib.sha256).digest()

sig = base64.urlsafe_b64encode(digest).decode().rstrip("=")

print(body)
print("signature=" + sig)