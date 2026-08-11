#!/usr/bin/env bash

set -euo pipefail

required_languages_available() {
  local languages
  local has_chi_sim=0
  local has_eng=0

  command -v tesseract >/dev/null 2>&1 || return 1
  languages="$(tesseract --list-langs 2>/dev/null || true)"
  while IFS= read -r language; do
    case "$language" in
      chi_sim) has_chi_sim=1 ;;
      eng) has_eng=1 ;;
    esac
  done <<< "$languages"
  [[ "$has_chi_sim" -eq 1 && "$has_eng" -eq 1 ]]
}

print_runtime_versions() {
  local version_output
  local version_line

  version_output="$(tesseract --version 2>/dev/null || true)"
  IFS= read -r version_line <<< "$version_output"
  version_line="${version_line#tesseract }"
  printf 'tesseract_version=%s\n' "${version_line:-unknown}"
  printf 'tesseract_languages=chi_sim+eng\n'
}

if required_languages_available; then
  print_runtime_versions
  exit 0
fi

if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  apt-get install -y tesseract-ocr tesseract-ocr-chi-sim
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y tesseract tesseract-langpack-chi_sim
elif command -v yum >/dev/null 2>&1; then
  yum install -y tesseract tesseract-langpack-chi_sim
else
  printf 'unsupported_package_manager\n' >&2
  exit 2
fi

if ! required_languages_available; then
  printf 'required_tesseract_languages_missing\n' >&2
  exit 1
fi

print_runtime_versions
