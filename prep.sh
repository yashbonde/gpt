#!/usr/bin/env bash

echo "Get merges and vocab"
python3 -c 'import requests
def get(url):
 fname = url.split("/")[-1]
 with open(fname, "w") as f:
  c = requests.get(url).content.decode("utf-8")
  f.write(c)

get("https://huggingface.co/gpt2/raw/main/merges.txt")
get("https://huggingface.co/gpt2/raw/main/vocab.json")'

echo "Load @yashbonde's gpt gists"
python3 -c '
def get_gist(id):
 import requests, json
 d = json.loads(requests.get(f"https://api.github.com/gists/{id}").content.decode("utf-8"))
 for file_name in d["files"]: # save all the files exactly like on your gist
  with open(file_name, "w") as f:
   print("Writing:", file_name)
   f.write(d["files"][file_name]["content"])

get_gist("62df9d16858a43775c22a6af00a8d707")
get_gist("cadb515b6c658f18147d948fac685c7b")'

echo "Testing Tokenizer"
python3 tokenizer.py

echo "==== Complete ===="
