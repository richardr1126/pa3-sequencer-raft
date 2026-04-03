#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-leader}"
NAMESPACE="${2:-default}"

if [[ "${MODE}" != "leader" && "${MODE}" != "follower" ]]; then
  echo "usage: $0 <leader|follower> [namespace]" >&2
  exit 1
fi

PODS="$(kubectl -n "${NAMESPACE}" get pods -l app=marketplace,component=db-product -o jsonpath='{.items[*].metadata.name}')"
if [[ -z "${PODS}" ]]; then
  echo "no db-product pods found in namespace ${NAMESPACE}" >&2
  exit 1
fi

LEADER_POD=""

for POD in ${PODS}; do
  STATUS="$(
    kubectl -n "${NAMESPACE}" exec "${POD}" -- python -c \
      "from pysyncobj.syncobj_admin import executeAdminCommand; print(executeAdminCommand(['-conn','127.0.0.1:11001','-status']))" \
      2>/dev/null || true
  )"

  LEADER_ADDR="$(printf '%s\n' "${STATUS}" | awk -F': ' '$1=="leader"{print $2; exit}')"
  if [[ -n "${LEADER_ADDR}" && "${LEADER_ADDR}" != "None" && "${LEADER_ADDR}" != "null" ]]; then
    for CANDIDATE in ${PODS}; do
      if [[ "${LEADER_ADDR}" == "${CANDIDATE}.marketplace-db-product:11001" ]]; then
        LEADER_POD="${CANDIDATE}"
        break
      fi
    done
  fi

  if [[ -n "${LEADER_POD}" ]]; then
    break
  fi

  # Fallback for status formats that don't emit a leader addr but expose leader state.
  STATE_VALUE="$(printf '%s\n' "${STATUS}" | awk -F': ' '$1=="state"{print $2; exit}')"
  if [[ "${STATE_VALUE}" == "2" || "${STATE_VALUE}" == "leader" || "${STATE_VALUE}" == "LEADER" ]]; then
    LEADER_POD="${POD}"
    break
  fi
done

if [[ -z "${LEADER_POD}" ]]; then
  LEADER_POD="$(printf '%s\n' "${PODS}" | awk '{print $1}')"
fi

if [[ "${MODE}" == "leader" ]]; then
  echo "${LEADER_POD}"
  exit 0
fi

for POD in ${PODS}; do
  if [[ "${POD}" != "${LEADER_POD}" ]]; then
    echo "${POD}"
    exit 0
  fi
done

echo "${LEADER_POD}"
