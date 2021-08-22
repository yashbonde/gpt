# @yashbonde x NBX Internal - 28th April 2021
# This file is based on code by the authors denoted below and has been modified from its original version.
#
# Copyright 2018 The Open AI Team Authors and The HuggingFace Inc. team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tokenization classes for OpenAI GPT."""

from __future__ import (absolute_import, division, print_function,
            unicode_literals)

import sys
import json
import logging
import os
import regex as re
from io import open

import torch

from functools import lru_cache
logger = logging.getLogger(__name__)

VOCAB_NAME = 'vocab.json'
MERGES_NAME = 'merges.txt'
SPECIAL_TOKENS_NAME = 'special_tokens.txt'
here = os.path.dirname(os.path.abspath(__file__))
VOCAB_FULL_PATH = os.path.join(here, VOCAB_NAME)
MERGES_FULL_PATH = os.path.join(here, MERGES_NAME)


@lru_cache()
def bytes_to_unicode():
  """
  Returns list of utf-8 byte and a corresponding list of unicode strings.
  The reversible bpe codes work on unicode strings.
  This means you need a large # of unicode characters in your vocab if you want to avoid UNKs.
  When you're at something like a 10B token dataset you end up needing around 5K for decent coverage.
  This is a signficant percentage of your normal, say, 32K bpe vocab.
  To avoid that, we want lookup tables between utf-8 bytes and unicode strings.
  And avoids mapping to whitespace/control characters the bpe code barfs on.
  """
  _chr = unichr if sys.version_info[0] == 2 else chr
  bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + \
    list(range(ord("®"), ord("ÿ") + 1))
  cs = bs[:]
  n = 0
  for b in range(2**8):
    if b not in bs:
      bs.append(b)
      cs.append(2**8 + n)
      n += 1
  cs = [_chr(n) for n in cs]
  return dict(zip(bs, cs))


def get_pairs(word):
  """Return set of symbol pairs in a word.
  Word is represented as tuple of symbols (symbols being variable-length strings).
  """
  pairs = set()
  prev_char = word[0]
  for char in word[1:]:
    pairs.add((prev_char, char))
    prev_char = char
  return pairs


class GPT2Tokenizer(object):
  def __init__(self, vocab_file = VOCAB_FULL_PATH, merges_file = MERGES_FULL_PATH, errors='replace',
         special_tokens=None, max_len=None):
    self.max_len = max_len if max_len is not None else int(1e12)
    self.encoder = json.load(open(vocab_file))
    self.decoder = {v: k for k, v in self.encoder.items()}
    self.errors = errors # how to handle errors in decoding
    self.byte_encoder = bytes_to_unicode()
    self.byte_decoder = {v: k for k, v in self.byte_encoder.items()}
    bpe_data = open(merges_file, encoding='utf-8').read().split('\n')[1:-1]
    bpe_merges = [tuple(merge.split()) for merge in bpe_data]
    self.bpe_ranks = dict(zip(bpe_merges, range(len(bpe_merges))))
    self.cache = {}

    # Should haved added re.IGNORECASE so BPE merges can happen for
    # capitalized versions of contractions
    self.pat = re.compile(r"""'s|'t|'re|'ve|'m|'ll|'d| ?\p{L}+| ?\p{N}+| ?[^\s\p{L}\p{N}]+|\s+(?!\S)|\s+""")

    self.special_tokens = {}
    self.special_tokens_decoder = {}
    self.set_special_tokens(special_tokens)

    self.eot_tag = "<|endoftext|>"
    self.eot_id = self.encoder["<|endoftext|>"]


  def __len__(self):
    return len(self.encoder) + len(self.special_tokens)

  def set_special_tokens(self, special_tokens):
    """ Add a list of additional tokens to the encoder.
      The additional tokens are indexed starting from the last index of the
      current vocabulary in the order of the `special_tokens` list.
    """
    if not special_tokens:
      self.special_tokens = {}
      self.special_tokens_decoder = {}
      return
    self.special_tokens = dict((tok, len(self.encoder) + i)
                  for i, tok in enumerate(special_tokens))
    self.special_tokens_decoder = {v: k for k, v in self.special_tokens.items()}
    logger.info("Special tokens {}".format(self.special_tokens))

  def bpe(self, token):
    if token in self.cache:
      return self.cache[token]
    word = tuple(token)
    pairs = get_pairs(word)

    if not pairs:
      return token

    while True:
      bigram = min(pairs, key=lambda pair: self.bpe_ranks.get(pair, float('inf')))
      if bigram not in self.bpe_ranks:
        break
      first, second = bigram
      new_word = []
      i = 0
      while i < len(word):
        try:
          j = word.index(first, i)
          new_word.extend(word[i:j])
          i = j
        except BaseException:
          new_word.extend(word[i:])
          break

        if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
          new_word.append(first + second)
          i += 2
        else:
          new_word.append(word[i])
          i += 1
      new_word = tuple(new_word)
      word = new_word
      if len(word) == 1:
        break
      else:
        pairs = get_pairs(word)
    word = ' '.join(word)
    self.cache[token] = word
    return word

  def tokenize(self, text):
    """ Tokenize a string. """
    bpe_tokens = []
    for token in re.findall(self.pat, text):
      if sys.version_info[0] == 2:
        token = ''.join(self.byte_encoder[ord(b)] for b in token)
      else:
        token = ''.join(self.byte_encoder[b] for b in token.encode('utf-8'))
      bpe_tokens.extend(bpe_token for bpe_token in self.bpe(token).split(' '))
    return bpe_tokens

  def convert_tokens_to_ids(self, tokens):
    """ Converts a sequence of tokens into ids using the vocab. """
    ids = []
    if isinstance(tokens, str) or (sys.version_info[0] == 2 and isinstance(tokens, unicode)):
      if tokens in self.special_tokens:
        return self.special_tokens[tokens]
      else:
        return self.encoder.get(tokens, 0)
    for token in tokens:
      if token in self.special_tokens:
        ids.append(self.special_tokens[token])
      else:
        ids.append(self.encoder.get(token, 0))
    if len(ids) > self.max_len:
      logger.warning(
        "Token indices sequence length is longer than the specified maximum "
        " sequence length for this OpenAI GPT model ({} > {}). Running this"
        " sequence through the model will result in indexing errors".format(
          len(ids), self.max_len)
      )
    return ids

  def convert_ids_to_tokens(self, ids, skip_special_tokens=False):
    """Converts a sequence of ids in BPE tokens using the vocab."""
    tokens = []
    for i in ids:
      if i in self.special_tokens_decoder:
        if not skip_special_tokens:
          tokens.append(self.special_tokens_decoder[i])
      else:
        tokens.append(self.decoder[i])
    return tokens

  def encode(self, text):
    return self.convert_tokens_to_ids(self.tokenize(text))

  def __call__(self, text):
    return torch.Tensor(self.encode(text)).long()

  def decode(self, tokens):
    if isinstance(tokens[0], list):
      return [self.decode(t) for t in tokens]
    text = ''.join([self.decoder[token] for token in tokens])
    text = bytearray([self.byte_decoder[c] for c in text]).decode('utf-8', errors=self.errors)
    return text

  def save_vocabulary(self, vocab_path):
    """Save the tokenizer vocabulary and merge files to a directory."""
    if not os.path.isdir(vocab_path):
      logger.error("Vocabulary path ({}) should be a directory".format(vocab_path))
      return
    vocab_file = os.path.join(vocab_path, VOCAB_NAME)
    merge_file = os.path.join(vocab_path, MERGES_NAME)
    special_tokens_file = os.path.join(vocab_path, SPECIAL_TOKENS_NAME)

    with open(vocab_file, 'w', encoding='utf-8') as f:
      f.write(json.dumps(self.encoder, ensure_ascii=False))

    index = 0
    with open(merge_file, "w", encoding="utf-8") as writer:
      writer.write(u'#version: 0.2\n')
      for bpe_tokens, token_index in sorted(self.bpe_ranks.items(), key=lambda kv: kv[1]):
        if index != token_index:
          logger.warning("Saving vocabulary to {}: BPE merge indices are not consecutive."
                  " Please check that the tokenizer is not corrupted!".format(merge_file))
          index = token_index
        writer.write(' '.join(bpe_tokens) + u'\n')
        index += 1

    index = len(self.encoder)
    with open(special_tokens_file, 'w', encoding='utf-8') as writer:
      for token, token_index in sorted(self.special_tokens.items(), key=lambda kv: kv[1]):
        if index != token_index:
          logger.warning("Saving special tokens vocabulary to {}: BPE indices are not consecutive."
                  " Please check that the tokenizer is not corrupted!".format(special_tokens_file))
          index = token_index
        writer.write(token + u'\n')
        index += 1

    return vocab_file, merge_file, special_tokens_file

if __name__ == "__main__":
  tokenizer = GPT2Tokenizer()

  # test dec(enc(x)) == x
  string = "When a self-driving car kills a pedestrian, prickly Cyber Cell detective Saajan Kundu teams up with his " \
    "estranged partner Laxmi Suri to investigate. But was this an accident?"
  encoded = tokenizer.encode(string)
  decoded = tokenizer.decode(encoded)
  assert decoded == string

  # test if our mods work
  encoded = tokenizer(string)
  encoded = torch.tile(encoded, [3, 1])
  tokenizer.decode(encoded.tolist())