#!/usr/bin/env bash
# All: one documented sequence rather than a second lifecycle implementation.
# Stop on the first failure (the root CFOS launcher sets `set -e`).
# Sourced by ../CFOS.
cfos_all() {
  cfos_setup
  cfos_build
  cfos_stage
  cfos_test
}
