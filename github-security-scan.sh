#!/bin/bash
# github-security-scan.sh
# Sicherheits-Scan für GitHub Repository und Account

set -euo pipefail

echo "🔒 GitHub Security Scanner für $(gh auth status 2>&1 | grep account | cut -d' ' -f2)"
echo ""

# 1. Repository scan
REPO_DIR="$(pwd)"
echo "📁 Repository Scan: $REPO_DIR"

# Check for secrets
echo "1. Geheime Schlüssel in Dateien suchen..."
SECRET_PATTERNS=(
    "ghp_[A-Za-z0-9_]{36,255}"
    "ghs_[A-Za-z0-9_]{36,255}"
    "github_pat_[A-Za-z0-9_]{82}"
    "AKIA[0-9A-Z]{16}"
    "ASIA[0-9A-Z]{16}"
    "ACCA[0-9A-Z]{16}"
    "arn:aws:[^:]+:[^:]+:[0-9]{12}:"
    "-----BEGIN.*PRIVATE KEY"
    "-----BEGIN.*RSA"
    "-----BEGIN.*EC"
    "-----BEGIN.*DSA"
)

PATHS_TO_SCAN="*.py *.md *.txt *.yaml *.yml *.json *.env* .env*"

ERROR_COUNT=0
for pattern in "${SECRET_PATTERNS[@]}"; do
    matches=$(grep -r -l -E "$pattern" $PATHS_TO_SCAN 2>/dev/null || true)
    if [[ -n "$matches" ]]; then
        echo "   ⛔ Mögliches Secret gefunden (Pattern: ${pattern:0:20}...)"
        echo "   Dateien: $matches"
        ERROR_COUNT=$((ERROR_COUNT + 1))
    fi
done

if [[ $ERROR_COUNT -eq 0 ]]; then
    echo "   ✅ Keine offensichtlichen Secrets gefunden"
fi

# 2. .gitignore check
echo ""
echo "2. .gitignore prüfen..."
if [[ -f ".gitignore" ]]; then
    if grep -q -E "(\.env|secret|password|credential)" .gitignore 2>/dev/null; then
        echo "   ✅ .gitignore enthält Schutz für Secrets"
    else
        echo "   ⚠  .gitignore enthält keinen expliziten Schutz für .env/secret-Dateien"
        echo "   Empfehlung: Füge diese Zeilen hinzu:"
        echo "   *.env"
        echo "   *.secret"
        echo "   *password*"
        echo "   *credential*"
    fi
else
    echo "   ⚠  Keine .gitignore Datei vorhanden"
fi

# 3. File permissions
echo ""
echo "3. Dateiberechtigungen prüfen..."
if [[ -f "tacx_180.py" && ! -x "tacx_180.py" ]]; then
    echo "   ⚠  tacx_180.py ist nicht ausführbar (chmod +x tacx_180.py)"
fi
if [[ -f "tacx_watt.py" && ! -x "tacx_watt.py" ]]; then
    echo "   ⚠  tacx_watt.py ist nicht ausführbar (chmod +x tacx_watt.py)"
fi

# 4. GitHub access check
echo ""
echo "4. GitHub Account-Status..."
if gh auth status &>/dev/null; then
    echo "   ✅ GitHub CLI authentifiziert"
    
    # Show repo visibility
    REPO_URL=$(git remote get-url origin 2>/dev/null || true)
    if [[ "$REPO_URL" == *"github.com"* ]]; then
        echo "   Repository: $(echo $REPO_URL | sed 's|https://github.com/||')"
        
        # Get repo info via API
        REPO_NAME=$(basename -s .git "$(git remote get-url origin 2>/dev/null)")
        ORG_USER=$(echo $REPO_URL | sed -n 's|.*github.com/\([^/]*\)/.*|\1|p')
        
        if [[ -n "$ORG_USER" && -n "$REPO_NAME" ]]; then
            if gh api "repos/$ORG_USER/$REPO_NAME" &>/dev/null; then
                echo "   ✅ Repository existiert und ist erreichbar"
            fi
        fi
    fi
else
    echo "   ⚠  GitHub CLI nicht authentifiziert"
fi

# 5. SSH Keys
echo ""
echo "5. SSH Schlüssel prüfen..."
if command -v ssh-add &>/dev/null; then
    SSH_KEYS=$(ssh-add -l 2>/dev/null | wc -l)
    echo "   $SSH_KEYS Schlüssel im SSH-Agent"
else
    echo "   ℹ️  SSH-Agent nicht verfügbar"
fi

# 6. Dependencies check
echo ""
echo "6. Abhängigkeiten prüfen..."
if [[ -f "requirements.txt" ]]; then
    echo "   ✅ requirements.txt gefunden"
    if grep -q "bleak" requirements.txt; then
        echo "   ✅ bleak als Abhängigkeit deklariert"
    fi
else
    echo "   ⚠  Keine requirements.txt gefunden"
fi

# 7. Python virtual environment check
echo ""
echo "7. Python Umgebung prüfen..."
if [[ -d "venv" ]]; then
    echo "   ✅ Virtual Environment (venv) vorhanden"
    if [[ -f "venv/bin/activate" ]]; then
        echo "   ✅ venv korrekt eingerichtet"
    fi
else
    echo "   ℹ️  Keine venv im Repository (normal für Source-Code)"
fi

# 8. GitHub Actions security
echo ""
echo "8. GitHub Actions..."
if [[ -d ".github/workflows" ]]; then
    echo "   ✅ GitHub Actions Workflows gefunden"
    # Check for potentially dangerous permissions
    for workflow in .github/workflows/*.yml .github/workflows/*.yaml; do
        if [[ -f "$workflow" ]]; then
            if grep -q "contents: write" "$workflow" || grep -q "actions: write" "$workflow"; then
                echo "   ⚠  Workflow $(basename $workflow) hat write-Berechtigungen"
            fi
        fi
    done
else
    echo "   ℹ️  Keine GitHub Actions Workflows gefunden"
fi

# 9. Branch protection
echo ""
echo "9. Branch Schutz (manuell prüfen):"
echo "   Öffne in Browser: https://github.com/jkbutterfinger/tacx-flow-bluetooth/settings/branches"
echo "   - ✅ Main Branch schützen"
echo "   - ✅ Require pull request reviews before merging"
echo "   - ✅ Require status checks to pass before merging"

# 10. Summary
echo ""
echo "📋 ZUSAMMENFASSUNG:"
echo "   - Repository Scan komplett"
echo "   - Secrets gescannt: $ERROR_COUNT potentielle Probleme"
echo ""
echo "🔒 EMPFOHLENE NÄCHSTE SCHRITTE:"
echo "   1. 2FA auf GitHub aktivieren: https://github.com/settings/security"
echo "   2. Alte SSH Keys entfernen: https://github.com/settings/keys"
echo "   3. Alte Personal Access Tokens löschen: https://github.com/settings/tokens"
echo "   4. Branch Protection aktivieren für main/master"
echo "   5. Dependabot Security Alerts aktivieren"
echo ""
echo "ℹ️  Dieser Scan ist nur eine Basisüberprüfung."
echo "    Für Produktionscode: gitleaks, trufflehog oder GitHub Advanced Security nutzen."

exit 0