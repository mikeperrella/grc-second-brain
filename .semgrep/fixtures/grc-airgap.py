# Test fixture for the Python rules in .semgrep/grc-airgap.yml. Run with:
#   semgrep --test --config .semgrep/ .semgrep/fixtures
# This file is never imported or executed. It is excluded from the main scan, because it
# violates the rules on purpose. Every "ruleid:" line must match its rule; every "ok:" line
# must not. All data here is synthetic.
#
# The directory is not called "tests": grc-py-httpx-client-outside-factory excludes
# "**/tests/**", so a "tests" directory would stop that rule from firing here.
# ruleid: grc-py-contradictions-network-process-import
import subprocess

# ruleid: grc-py-contradictions-network-process-import
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
# ruleid: grc-py-forbidden-network-imports, grc-py-contradictions-network-process-import
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

# ---------- grc-py-chat-endpoint-outside-generation-client ----------
# ruleid: grc-py-chat-endpoint-outside-generation-client
chat_url = "http://127.0.0.1:11434/api/chat"
# ruleid: grc-py-chat-endpoint-outside-generation-client
chat_no_slash = urljoin(base, "api/chat")
# ruleid: grc-py-chat-endpoint-outside-generation-client
chat_shouting = "/API/Chat"
# ruleid: grc-py-chat-endpoint-outside-generation-client
chat_doubled = "/api//chat"
# ruleid: grc-py-chat-endpoint-outside-generation-client
chat_dotted = "/api/./chat"
# ok: grc-py-chat-endpoint-outside-generation-client
chat_tags_url = "http://127.0.0.1:11434/api/tags"
# ok: grc-py-chat-endpoint-outside-generation-client
chat_embed_url = "http://127.0.0.1:11434/api/embed"
# ok: grc-py-chat-endpoint-outside-generation-client
chat_longer_word = "/api/chatter"
# ok: grc-py-chat-endpoint-outside-generation-client
chat_prose = "the support chat is open"

# ---------- grc-py-load-workbook-outside-loader ----------
# ruleid: grc-py-load-workbook-outside-loader
from openpyxl import load_workbook
# ruleid: grc-py-load-workbook-outside-loader
book = openpyxl.load_workbook(path)
# ruleid: grc-py-load-workbook-outside-loader
book = load_workbook(BytesIO(data), read_only=False)
# ruleid: grc-py-load-workbook-outside-loader
from openpyxl.reader.excel import load_workbook as read_book
# ok: grc-py-load-workbook-outside-loader
book_count = len(workbooks)
# ok: grc-py-load-workbook-outside-loader
loaders = load_workbooks_report
# ok: grc-py-load-workbook-outside-loader
loader = questionnaire_loader.load_questionnaire(data)

# ---------- grc-py-csv-outside-reality ----------
# ruleid: grc-py-csv-outside-reality
import csv
# ruleid: grc-py-csv-outside-reality
from csv import reader
# ruleid: grc-py-csv-outside-reality
import csv as table
# ruleid: grc-py-csv-outside-reality
import os, csv
# ruleid: grc-py-csv-outside-reality
import csv, os
# ruleid: grc-py-csv-outside-reality
import json, os as operating_system, csv
if True:
    # ruleid: grc-py-csv-outside-reality
    import csv
# ok: grc-py-csv-outside-reality
import csvkit_like
# ok: grc-py-csv-outside-reality
import os, csvkit
# ok: grc-py-csv-outside-reality
import mycsv
# ok: grc-py-csv-outside-reality
from csvkit import reader as kit_reader
# ok: grc-py-csv-outside-reality
csv_rows = 0
# ok: grc-py-csv-outside-reality
note = "export the csv report"

# ---------- grc-py-workbook-save-outside-writer ----------
# ruleid: grc-py-workbook-save-outside-writer
workbook.save(buffer)
# ruleid: grc-py-workbook-save-outside-writer
loaded.workbook.save(path)
# ok: grc-py-workbook-save-outside-writer
saved_flag = True
# ok: grc-py-workbook-save-outside-writer
autosave_every = 30

# ---------- grc-py-questionnaire-file-write-call ----------
# ruleid: grc-py-questionnaire-file-write-call
target.write_bytes(data)
# ruleid: grc-py-questionnaire-file-write-call
target.write_text(text, encoding="utf-8")
# ruleid: grc-py-questionnaire-file-write-call
os.replace(temp_name, final_name)
# ruleid: grc-py-questionnaire-file-write-call
os.rename(temp_name, final_name)
# ruleid: grc-py-questionnaire-file-write-call
shutil.copyfile(source, destination)
# ruleid: grc-py-questionnaire-file-write-call
shutil.copy2(source, destination)
# ruleid: grc-py-questionnaire-file-write-call
shutil.move(source, destination)
# ok: grc-py-questionnaire-file-write-call
data = target.read_bytes()
# ok: grc-py-questionnaire-file-write-call
exists = os.path.exists(target)
# ok: grc-py-questionnaire-file-write-call
renamed_count = 0

# ---------- grc-py-questionnaire-open-mode ----------
# ruleid: grc-py-questionnaire-open-mode
handle = open(path, "wb")
# ruleid: grc-py-questionnaire-open-mode
handle = open(path, "w", encoding="utf-8")
# ruleid: grc-py-questionnaire-open-mode
handle = open(Path(root) / name, mode="ab")
# ruleid: grc-py-questionnaire-open-mode
handle = open(path, 'r+b')
# ruleid: grc-py-questionnaire-open-mode
handle = path.open("w")
# ok: grc-py-questionnaire-open-mode
handle = open(path, "rb")
# ok: grc-py-questionnaire-open-mode
handle = open(path, "xb")
# ok: grc-py-questionnaire-open-mode
handle = open(Path(root) / name, mode="xb")
# ok: grc-py-questionnaire-open-mode
stream = archive.open(info)
# ok: grc-py-questionnaire-open-mode
text = open(path).read()

# ---------- grc-py-questionnaire-delete-outside-writer ----------
# ruleid: grc-py-questionnaire-delete-outside-writer
os.remove(path)
# ruleid: grc-py-questionnaire-delete-outside-writer
os.unlink(path)
# ruleid: grc-py-questionnaire-delete-outside-writer
path.unlink()
# ruleid: grc-py-questionnaire-delete-outside-writer
path.unlink(missing_ok=True)
# ok: grc-py-questionnaire-delete-outside-writer
unlinked_total = 0
# ok: grc-py-questionnaire-delete-outside-writer
removed = items.remove_all

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

# ---------- grc-py-ledger-mutation-outside-ledger ----------
# ruleid: grc-py-ledger-mutation-outside-ledger
sql_one = "UPDATE promise SET status = 'PARTIAL' WHERE promise_id = ?"
# ruleid: grc-py-ledger-mutation-outside-ledger
sql_two = "DELETE FROM promise_event WHERE promise_id = ?"
# ruleid: grc-py-ledger-mutation-outside-ledger
sql_three = "UPDATE customer SET display_name = ?"
# ruleid: grc-py-ledger-mutation-outside-ledger
sql_four = "INSERT OR REPLACE INTO run_record (run_id) VALUES (?)"
# ruleid: grc-py-ledger-mutation-outside-ledger
sql_five = "REPLACE INTO run_answer (run_id) VALUES (?)"
# ruleid: grc-py-ledger-mutation-outside-ledger
sql_six = """DELETE
    FROM schema_info
"""
# ok: grc-py-ledger-mutation-outside-ledger
sql_read = "SELECT promise_id FROM promise WHERE customer_id = ?"
# ok: grc-py-ledger-mutation-outside-ledger
sql_insert = "INSERT INTO promise_event (promise_id) VALUES (?)"
# ok: grc-py-ledger-mutation-outside-ledger
sql_other_table = "UPDATE settings SET value = ?"
# ok: grc-py-ledger-mutation-outside-ledger
sql_similar_name = "DELETE FROM promise_drafts"
# ok: grc-py-ledger-mutation-outside-ledger
# we never update customer names, and delete from promise_event is blocked
note = 1
# ok: grc-py-ledger-mutation-outside-ledger
PROSE = """We never update customer names. A delete from promise_event is blocked by a trigger."""

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

# ---------- grc-py-contradictions-network-process-import ----------
# ruleid: grc-py-contradictions-network-process-import
import socket
# ruleid: grc-py-contradictions-network-process-import
import urllib.request
# ruleid: grc-py-contradictions-network-process-import
from http import client as http_client
# ruleid: grc-py-contradictions-network-process-import
from subprocess import run as run_process
# ruleid: grc-py-contradictions-network-process-import
import multiprocessing
# ruleid: grc-py-contradictions-network-process-import
import asyncio.subprocess
# ruleid: grc-py-contradictions-network-process-import
from grc_sidecar.security import net
# ruleid: grc-py-contradictions-network-process-import
from grc_sidecar.security.net import create_async_client
# ruleid: grc-py-contradictions-network-process-import
import grc_sidecar.security.net
# ok: grc-py-contradictions-network-process-import
import httpx_like_name
# ok: grc-py-contradictions-network-process-import
import socketlike
# ok: grc-py-contradictions-network-process-import
from grc_sidecar.contradictions.models import Pair
# ok: grc-py-contradictions-network-process-import
from grc_sidecar.security import paths
# ok: grc-py-contradictions-network-process-import
from httplib_notes import table

# ---------- grc-py-contradictions-llm-outside-model-module ----------
# ruleid: grc-py-contradictions-llm-outside-model-module
from grc_sidecar.llm.prompt import neutralize
# ruleid: grc-py-contradictions-llm-outside-model-module
import grc_sidecar.llm
# ruleid: grc-py-contradictions-llm-outside-model-module
from grc_sidecar.llm import generation_client
# ruleid: grc-py-contradictions-llm-outside-model-module
from grc_sidecar import llm
# ruleid: grc-py-contradictions-llm-outside-model-module
import grc_sidecar.llm.retry
# ok: grc-py-contradictions-llm-outside-model-module
from grc_sidecar.contradictions.verdict import ModelAsker
# ok: grc-py-contradictions-llm-outside-model-module
from grc_sidecar import errors
# ok: grc-py-contradictions-llm-outside-model-module
import grc_sidecar_llm_notes
# ok: grc-py-contradictions-llm-outside-model-module
from grc_sidecar.questionnaire.grounding import long_enough
