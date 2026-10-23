#!/usr/bin/env bash
set -euo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
idric_repo=${IDRIC_REPO:-"$repo_root/.idric"}
compiler_ref=${IDRIC_COMPILER_REF:-$(tr -d '\n' < "$repo_root/IDRIC_COMPILER_REF")}
artifact_root=${IDRIC_X86_ARTIFACTS_DIR:-"$repo_root/build/checked-x86"}
compiler="$idric_repo/_/edric"

if [[ ! -x $compiler ]]; then
  echo "FAIL compiler_available: $compiler is not executable" >&2
  exit 1
fi
actual_compiler_revision=$(git -C "$idric_repo" rev-parse HEAD)
if [[ $(uname -s) != Linux || $(uname -m) != x86_64 ]]; then
  echo "FAIL native_host: x86_64 Linux is required" >&2
  exit 1
fi
for command in cmp dd file git objdump od python3 readelf sha256sum; do
  command -v "$command" >/dev/null || { echo "FAIL validator_available: $command" >&2; exit 1; }
done

mkdir -p "$artifact_root"

while read -r fixture expected_hex; do
  [[ -n $fixture ]] || continue
  name=${fixture%.idric}
  source="$repo_root/fixtures/$fixture"
  directory="$artifact_root/$name"
  artifact="$directory/$name.one-step"
  second_artifact="$directory/$name.second.one-step"
  executable="$directory/$name.elf"
  second_executable="$directory/$name.second.elf"
  listing="$directory/$name.instructions"
  second_listing="$directory/$name.second.instructions"
  receipt="$directory/$name.execution"
  stdout="$directory/$name.stdout"
  stderr="$directory/$name.stderr"
  status_file="$directory/pipeline.status"
  mkdir -p "$directory"

  "$compiler" --emit-one-step "$source" -o "$artifact"
  "$compiler" --emit-one-step "$source" -o "$second_artifact"
  cmp "$artifact" "$second_artifact"
  grep -Fx "$(printf 'compiler_head\tisomorphisms/Idric\t%s' "$actual_compiler_revision")" "$artifact" >/dev/null
  grep -Fx "$(printf 'core_typecheck\tPASS')" "$artifact" >/dev/null
  grep -Fx "$(printf 'representation\tidris2-anf-show-0.8.0')" "$artifact" >/dev/null

  python3 "$repo_root/backend/idric_x86.py" "$artifact" \
    --source "$source" --output "$executable" --listing "$listing" \
    --run-receipt "$receipt" --expect-stdout-hex "$expected_hex"
  python3 "$repo_root/backend/idric_x86.py" "$artifact" \
    --source "$source" --output "$second_executable" --listing "$second_listing"
  cmp "$executable" "$second_executable"
  cmp "$listing" "$second_listing"

  # The SAME checked compiler-owned artifact also cross-compiles to NetBSD.
  # Do not execute this binary on Linux or call cross-codegen native acceptance.
  netbsd_executable="$directory/$name.netbsd.elf"
  netbsd_repeat="$directory/$name.netbsd.repeat.elf"
  netbsd_listing="$directory/$name.netbsd.instructions"
  python3 "$repo_root/backend/idric_x86.py" "$artifact" \
    --source "$source" --target netbsd-x86_64 \
    --output "$netbsd_executable" --listing "$netbsd_listing"
  python3 "$repo_root/backend/idric_x86.py" "$artifact" \
    --source "$source" --target netbsd-x86_64 \
    --output "$netbsd_repeat"
  cmp "$netbsd_executable" "$netbsd_repeat"
  readelf -h -l -n "$netbsd_executable" > "$directory/netbsd-readelf.txt"
  grep -F 'IDENT 1000000000' "$directory/netbsd-readelf.txt" >/dev/null
  grep -F 'NetBSD' "$directory/netbsd-readelf.txt" >/dev/null
  grep -F 'syscall ; write' "$netbsd_listing" >/dev/null
  grep -F 'jc process:write_failure' "$netbsd_listing" >/dev/null

  file "$executable" > "$directory/file.txt"
  readelf -h -l -S -d "$executable" > "$directory/readelf.txt"
  grep -F 'ELF 64-bit' "$directory/file.txt" >/dev/null
  grep -F 'x86-64' "$directory/file.txt" >/dev/null
  grep -E 'Type:[[:space:]]+EXEC' "$directory/readelf.txt" >/dev/null
  grep -E 'Machine:[[:space:]]+Advanced Micro Devices X86-64' "$directory/readelf.txt" >/dev/null
  grep -E 'Entry point address:[[:space:]]+0x401000' "$directory/readelf.txt" >/dev/null
  grep -E 'Number of program headers:[[:space:]]+1' "$directory/readelf.txt" >/dev/null
  grep -F 'There are no sections in this file.' "$directory/readelf.txt" >/dev/null
  grep -F 'There is no dynamic section in this file.' "$directory/readelf.txt" >/dev/null

  dd if="$executable" of="$directory/code.bin" bs=1 skip=4096 status=none
  objdump -D -b binary -m i386:x86-64 -Mintel --adjust-vma=0x401000 \
    "$directory/code.bin" > "$directory/objdump.txt"

  set +e
  "$executable" > "$stdout" 2> "$stderr"
  exit_status=$?
  set -e
  actual_hex=$(od -An -tx1 -v "$stdout" | tr -d ' \n')
  [[ $exit_status -eq 0 ]] || { echo "FAIL $name native_execution: exit=$exit_status" >&2; exit 1; }
  [[ ! -s $stderr ]] || { echo "FAIL $name stderr: expected empty" >&2; exit 1; }
  [[ $actual_hex == "$expected_hex" ]] || {
    echo "FAIL $name semantic_result: expected=$expected_hex actual=$actual_hex" >&2
    exit 1
  }
  grep -Fx "$(printf 'stage\tsemantic_result\tPASS\t%s' "$expected_hex")" "$receipt" >/dev/null

  sha256sum "$source" "$artifact" "$listing" "$executable" \
    "$netbsd_executable" "$netbsd_listing" "$directory/netbsd-readelf.txt" \
    "$directory/readelf.txt" "$directory/objdump.txt" "$stdout" > "$directory/hashes.sha256"
  {
    printf 'repository\tisomorphisms/Idric\n'
    printf 'requested_ref\t%s\n' "$compiler_ref"
    printf 'resolved_sha\t%s\n' "$actual_compiler_revision"
    printf 'dirty_state\t%s\n' "$(if git -C "$idric_repo" status --porcelain | grep -q .; then printf dirty; else printf clean; fi)"
    printf 'stage\tsource_checked\tPASS\n'
    printf 'stage\tcompiler_handoff_emitted\tPASS\n'
    printf 'stage\tbackend_lowering\tPASS\n'
    printf 'stage\tnetbsd_cross_codegen\tPASS\tnative_execution=NOT_RUN\n'
    printf 'stage\tx86_encoding\tPASS\n'
    printf 'stage\telf_generated\tPASS\n'
    printf 'stage\telf_independently_validated\tPASS\n'
    printf 'stage\tnative_execution\tPASS\texit=0\n'
    printf 'stage\tsemantic_result\tPASS\tstdout_hex=%s\n' "$actual_hex"
    printf 'stage\tdeterministic_regeneration\tPASS\n'
  } > "$status_file"
  printf '%-18s PASS stdout_hex=%s exit=0\n' "$name" "$actual_hex"
done <<'FIXTURES'
PrintX.idric 58
Add.idric 13
Subtract.idric 05
Multiply.idric 54
BranchTrue.idric 29
BranchFalse.idric 63
DirectCall.idric 29
RegisterPressure.idric 15
FIXTURES

printf 'checked Idriç to direct x86-64 ELF64: PASS\n'
