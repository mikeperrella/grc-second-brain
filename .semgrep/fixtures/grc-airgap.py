# Test fixture for the Python rules in .semgrep/grc-airgap.yml. Run with:
#   semgrep --test --config .semgrep/ .semgrep/fixtures
# This file is never imported or executed. It is excluded from the main scan, because it
# violates the rules on purpose. Every "ruleid:" line must match its rule; every "ok:" line
# must not. All data here is synthetic.
#
# The directory is not called "tests": grc-py-httpx-client-outside-factory excludes
# "**/tests/**", so a "tests" directory would stop that rule from firing here.
import subprocess

import httpx

# ---------- grc-py-subprocess-shell-true ----------
# ruleid: grc-py-subprocess-shell-true
subprocess.run("echo hello", shell=True)
# ruleid: grc-py-subprocess-shell-true
subprocess.Popen("echo hello", shell=True)
# ok: grc-py-subprocess-shell-true
subprocess.run(["echo", "hello"], shell=False, timeout=5)

# ---------- grc-py-os-shell ----------
# ruleid: grc-py-os-shell
os.system("echo hello")
# ruleid: grc-py-os-shell
os.popen("echo hello")
# ok: grc-py-os-shell
os.getcwd()

# ---------- grc-py-eval-exec ----------
# ruleid: grc-py-eval-exec
eval("1 + 1")
# ruleid: grc-py-eval-exec
exec("x = 1")
# ok: grc-py-eval-exec
ast.literal_eval("1")

# ---------- grc-py-bind-all-interfaces ----------
# ruleid: grc-py-bind-all-interfaces
uvicorn.run(app, host="0.0.0.0", port=0)
# ruleid: grc-py-bind-all-interfaces
uvicorn.run(app, host="::", port=0)
# ruleid: grc-py-bind-all-interfaces
uvicorn.Config(app, host="0.0.0.0", port=0)
# ruleid: grc-py-bind-all-interfaces
uvicorn.Config(app, host="::", port=0)
# ok: grc-py-bind-all-interfaces
uvicorn.run(app, host="127.0.0.1", port=0)
# ok: grc-py-bind-all-interfaces
uvicorn.Config(app, host="127.0.0.1", port=0)

# ---------- grc-py-httpx-client-outside-factory ----------
# A direct client is always flagged here, even with trust_env=False (the factory is the only
# allowed place). The same lines are used below for grc-py-httpx-trust-env.
# ruleid: grc-py-httpx-client-outside-factory
client_a = httpx.Client(trust_env=False)
# ruleid: grc-py-httpx-client-outside-factory
client_b = httpx.AsyncClient(trust_env=False)
# ok: grc-py-httpx-client-outside-factory
timeout = httpx.Timeout(5.0)

# ---------- grc-py-httpx-trust-env ----------
# Both rules fire when trust_env=False is missing.
# ruleid: grc-py-httpx-client-outside-factory, grc-py-httpx-trust-env
client_c = httpx.Client(base_url="http://127.0.0.1:1")
# ruleid: grc-py-httpx-client-outside-factory, grc-py-httpx-trust-env
client_d = httpx.AsyncClient(base_url="http://127.0.0.1:1")
# With trust_env=False only the factory rule fires (client_a and client_b above); a trust-env
# match on those lines would fail the test, because they carry no "grc-py-httpx-trust-env"
# annotation.

# ---------- grc-py-module-level-http ----------
# ruleid: grc-py-module-level-http
urllib.request.urlopen("http://127.0.0.1:1/")
# ruleid: grc-py-module-level-http
httpx.get("http://127.0.0.1:1/")
# ruleid: grc-py-module-level-http
httpx.post("http://127.0.0.1:1/", json={})
# ruleid: grc-py-module-level-http
requests.get("http://127.0.0.1:1/")
# ok: grc-py-module-level-http
httpx.Timeout(1.0)

# ---------- grc-py-forbidden-network-imports ----------
# ruleid: grc-py-forbidden-network-imports
import openai
# ruleid: grc-py-forbidden-network-imports
import anthropic
# ruleid: grc-py-forbidden-network-imports
from boto3 import client
# ruleid: grc-py-forbidden-network-imports
import requests
# ruleid: grc-py-forbidden-network-imports
from pinecone import Index
# ok: grc-py-forbidden-network-imports
import json
# ok: grc-py-forbidden-network-imports
from pathlib import Path

# ---------- grc-py-ollama-pull ----------
# ruleid: grc-py-ollama-pull
pull_url = "http://127.0.0.1:11434/api/pull"
# ruleid: grc-py-ollama-pull
no_slash = urljoin(base, "api/pull")
# ruleid: grc-py-ollama-pull
shouting = "/API/Pull"
# ruleid: grc-py-ollama-pull
doubled = "/api//pull"
# ruleid: grc-py-ollama-pull
dotted = "/api/./pull"
# ruleid: grc-py-ollama-pull
encoded = "/api/%70ull"
# ruleid: grc-py-ollama-pull
client.pull(model)
# ruleid: grc-py-ollama-pull
ollama.pull("llama3.1:8b")
# ok: grc-py-ollama-pull
tags_url = "http://127.0.0.1:11434/api/tags"
# ok: grc-py-ollama-pull
embed_url = "http://127.0.0.1:11434/api/embed"
# ok: grc-py-ollama-pull
unrelated = "/api/pullover-docs"
# ok: grc-py-ollama-pull
queue.pop()

# ---------- grc-py-yaml-unsafe-load ----------
# ruleid: grc-py-yaml-unsafe-load
yaml.load(text)
# ruleid: grc-py-yaml-unsafe-load
yaml.load(text, Loader=yaml.SafeLoader)
# ruleid: grc-py-yaml-unsafe-load
yaml.load_all(text)
# ruleid: grc-py-yaml-unsafe-load
yaml.unsafe_load(text)
# ruleid: grc-py-yaml-unsafe-load
yaml.full_load(text)
# ruleid: grc-py-yaml-unsafe-load
parsed = yaml.parse(text, Loader=yaml.UnsafeLoader)
# ruleid: grc-py-yaml-unsafe-load
parsed = yaml.parse(text, Loader=yaml.CLoader)
# ruleid: grc-py-yaml-unsafe-load
from yaml import FullLoader
# ok: grc-py-yaml-unsafe-load
yaml.safe_load(text)
# ok: grc-py-yaml-unsafe-load
parsed = yaml.parse(text, Loader=yaml.SafeLoader)
# ok: grc-py-yaml-unsafe-load
raise yaml.YAMLError
# ok: grc-py-yaml-unsafe-load
from yaml import SafeLoader

# ---------- grc-py-sqlite-outside-repositories ----------
# ruleid: grc-py-sqlite-outside-repositories
import sqlite3
# ruleid: grc-py-sqlite-outside-repositories
import sqlite_vec
# ruleid: grc-py-sqlite-outside-repositories
from sqlite3 import connect
# ruleid: grc-py-sqlite-outside-repositories
from sqlite_vec import serialize_float32
# ok: grc-py-sqlite-outside-repositories
import sqlalchemy
# ok: grc-py-sqlite-outside-repositories
from grc_sidecar.repositories.base import VectorRepository

# ---------- grc-py-extension-load-outside-repositories ----------
# ruleid: grc-py-extension-load-outside-repositories
connection.load_extension("vec0")
# ruleid: grc-py-extension-load-outside-repositories
connection.enable_load_extension(True)
# ruleid: grc-py-extension-load-outside-repositories
sqlite_vec.load(connection)
# ok: grc-py-extension-load-outside-repositories
connection.execute("select 1")
# ok: grc-py-extension-load-outside-repositories
plugin_loader.load(connection)
