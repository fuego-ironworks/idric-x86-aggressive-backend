ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
AICI_ROOT ?= $(ROOT)/.aici-host
ICK ?=
ICK_FLAGS ?= -fno-link-libatomic

.PHONY: verify
verify:
	test -x "$(ICK)"
	mkdir -p "$(ROOT)/build/contract"
	"$(ICK)" $(ICK_FLAGS) -std=c11 -O2 -Wall -Wextra -Werror "$(AICI_ROOT)/src/aici.c" -o "$(ROOT)/build/contract/aici"
	"$(ROOT)/build/contract/aici" verify "$(AICI_ROOT)/contracts/build-toolchain-v0.contract.tsv" "$(ROOT)"
	cat "$(ROOT)/ci/build-toolchain.tsv"
