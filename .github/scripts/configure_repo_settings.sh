#!/usr/bin/env bash
# Idempotent script to configure progiwell/debrid-hub repository settings and branch rulesets.
set -euo pipefail

REPO="${1:-progiwell/debrid-hub}"

echo "Configuring repository merge & cleanup settings for ${REPO}..."
gh api -X PATCH "repos/${REPO}" \
  -f allow_squash_merge=true \
  -f allow_merge_commit=false \
  -f allow_rebase_merge=false \
  -f delete_branch_on_merge=true

echo "Ensuring 'Protect main and prod' ruleset exists on ${REPO}..."
EXISTING_ID=$(gh api "repos/${REPO}/rulesets" --jq '.[] | select(.name == "Protect main and prod") | .id' | head -n1 || true)

PAYLOAD=$(cat <<'JSON'
{
  "name": "Protect main and prod",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    {
      "actor_id": 5,
      "actor_type": "RepositoryRole",
      "bypass_mode": "always"
    }
  ],
  "conditions": {
    "ref_name": {
      "include": ["refs/heads/main", "refs/heads/prod"],
      "exclude": []
    }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "required_status_checks": [
          { "context": "test" },
          { "context": "Secret Scan (Gitleaks)" },
          { "context": "Python SAST, Linter & Dependency Audit" },
          { "context": "Extension Typecheck & Manifest Permission Audit" },
          { "context": "Container Image Scan (Trivy)" }
        ]
      }
    }
  ]
}
JSON
)

if [ -n "${EXISTING_ID}" ]; then
  echo "Updating existing ruleset ${EXISTING_ID}..."
  gh api -X PUT "repos/${REPO}/rulesets/${EXISTING_ID}" --input - <<< "${PAYLOAD}"
else
  echo "Creating new ruleset..."
  gh api -X POST "repos/${REPO}/rulesets" --input - <<< "${PAYLOAD}"
fi
