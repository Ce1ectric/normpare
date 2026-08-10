#!/usr/bin/env python3
"""Regression harness for normpare -- interface skeleton (not implemented yet)."""
from __future__ import annotations


def sha256_file(path):
    raise NotImplementedError


def snapshot(out_dir):
    raise NotImplementedError


def build_baseline(out_dir):
    raise NotImplementedError


def write_baseline(baseline, path):
    raise NotImplementedError


def read_baseline(path):
    raise NotImplementedError


def check(out_dir, baseline_path, explain=False):
    raise NotImplementedError


def main(argv=None):
    raise NotImplementedError
