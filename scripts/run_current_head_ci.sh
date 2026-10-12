#!/usr/bin/env bash
set -Eeuo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
idric_repo=${IDRIC_REPO:-"$repo_root/.idric"}
idric_support_root="$idric_repo/_"
idric_compiler="$idric_support_root/build/exec/idris2"
artifact_root=${IDRIC_X86_ARTIFACTS_DIR:-"$repo_root/build/checked-x86"}
compiler_ref=${IDRIC_COMPILER_REF:-$(tr -d '\n' < "$repo_root/IDRIC_COMPILER_REF")}
receipt="$artifact_root/current-head-receipt.tsv"
log="$artifact_root/current-head.log"
current_stage=compiler_build
passed=

backend_sha=$(git -C "$repo_root" rev-parse HEAD)
compiler_sha=$(git -C "$idric_repo" rev-parse HEAD)
backend_dirty=$(if git -C "$repo_root" status --porcelain | grep -q .; then printf dirty; else printf clean; fi)
compiler_dirty=$(if git -C "$idric_repo" status --porcelain | grep -q .; then printf dirty; else printf clean; fi)

mkdir -p "$artifact_root"
: > "$log"

write_receipt() {
  outcome=$1
  diagnostic=${2:-none}
  {
    printf 'CURRENT_HEAD_COMPATIBILITY\t1\n'
    printf 'repository\tfuego-ironworks/idric-x86-aggressive-backend\n'
    printf 'requested_ref\t%s\n' "${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-local}}"
    printf 'resolved_sha\t%s\n' "$backend_sha"
    printf 'dirty_state\t%s\n' "$backend_dirty"
    printf 'dependent_repository\tisomorphisms/Idric\n'
    printf 'dependent_requested_ref\t%s\n' "$compiler_ref"
    printf 'dependent_resolved_sha\t%s\n' "$compiler_sha"
    printf 'dependent_dirty_state\t%s\n' "$compiler_dirty"
    for stage in compiler_checkout compiler_build backend_unit one_step_handoff target_generation native_execution; do
      if [[ " $passed " == *" $stage "* ]]; then
        printf 'stage\t%s\tPASS\n' "$stage"
      elif [[ $stage == "$current_stage" ]]; then
        printf 'stage\t%s\t%s\n' "$stage" "$outcome"
      else
        printf 'stage\t%s\tSKIP\tprerequisite_not_met\n' "$stage"
      fi
    done
    if [[ $outcome == FAIL ]]; then
      printf 'first_failure\t%s\t%s\n' "$current_stage" "$diagnostic"
    else
      printf 'first_failure\tnone\n'
    fi
  } > "$receipt"
}

fail_receipt() {
  status=$?
  trap - ERR
  diagnostic=$(grep -E '(^FAIL|^Error:|^usage:|unsupported|rejected|not found|No such file)' "$log" | tail -n 1 || true)
  [[ -n $diagnostic ]] || diagnostic=$(tail -n 1 "$log" | tr '\t\r\n' '   ')
  write_receipt FAIL "${diagnostic:-exit_$status}"
  cat "$receipt" >&2
  exit "$status"
}
trap fail_receipt ERR

passed="compiler_checkout"
current_stage=compiler_build
if [[ ! -x "$idric_compiler" ]]; then
  "$idric_support_root/edric" bootstrap 2>&1 | tee -a "$log"
fi
"$idric_compiler" --version 2>&1 | tee -a "$log"
passed="$passed compiler_build"

current_stage=backend_unit
make -C "$repo_root" unit 2>&1 | tee -a "$log"
passed="$passed backend_unit"

current_stage=one_step_handoff
make -C "$repo_root" integration IDRIC_REPO="$idric_repo" IDRIC_COMPILER_REF="$compiler_ref" 2>&1 | tee -a "$log"
passed="$passed one_step_handoff target_generation native_execution"
current_stage=complete
write_receipt PASS none
cat "$receipt"
