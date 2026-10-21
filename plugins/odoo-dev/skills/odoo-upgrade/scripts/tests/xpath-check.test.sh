#!/usr/bin/env bash
# xpath-check.test.sh — offline tests for xpath-check.py.
#
# A stub `odoo.tools.template_inheritance` package on PYTHONPATH replaces
# Odoo. Its apply_inheritance_specs uses xml.etree.ElementTree and finds a
# simple `//tag[@name='x']` expression; it raises ValueError when none matches.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUT="$(cd "$SCRIPT_DIR/.." && pwd)/xpath-check.py"
work="$(mktemp -d "${TMPDIR:-/tmp}/xpath-check-test.XXXXXX")"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0
expect() { if [ "$2" = "$3" ]; then pass=$((pass+1)); else fail=$((fail+1)); echo "FAIL $1: wanted '$3', got '$2'" >&2; fi; }
jq_py() { python3 -I -c "import json,sys; d=json.loads(sys.argv[1]); print($2)" "$1"; }

stub="$work/stub/odoo/tools"
mkdir -p "$stub"
: > "$work/stub/odoo/__init__.py"
: > "$stub/__init__.py"
cat > "$stub/template_inheritance.py" <<'PY'
def apply_inheritance_specs(source, specs):
    for spec in specs:
        if spec.tag != "xpath":
            continue
        expr = spec.get("expr")
        if source.find("." + expr if expr.startswith("//") else expr) is None:
            raise ValueError(
                "Element '<xpath expr=\"%s\">' cannot be located in parent view" % expr
            )
    return source
PY

src="$work/src/base"; mkdir -p "$src/static/src/xml"
echo "{'name': 'base'}" > "$src/__manifest__.py"
cat > "$src/views.xml" <<'XML'
<odoo>
  <record id="view_form" model="ir.ui.view">
    <field name="name">base.form</field>
    <field name="arch" type="xml"><form><field name="name"/></form></field>
  </record>
</odoo>
XML
cat > "$src/static/src/xml/t.xml" <<'XML'
<templates><t t-name="base.Widget"><div><span class="lbl"/></div></t></templates>
XML

wt="$work/wt"; mod="$wt/acme"; mkdir -p "$mod/views" "$mod/static/src/xml" "$wt/other"
echo "{'name': 'Acme'}" > "$mod/__manifest__.py"
echo "{'name': 'Other'}" > "$wt/other/__manifest__.py"
cat > "$mod/views/v.xml" <<'XML'
<odoo>
  <record id="view_ok" model="ir.ui.view">
    <field name="inherit_id" ref="base.view_form"/>
    <field name="arch" type="xml"><data>
      <xpath expr="//field[@name='name']" position="after"><field name="x"/></xpath>
    </data></field>
  </record>
  <record id="view_bad" model="ir.ui.view">
    <field name="inherit_id" ref="base.view_form"/>
    <field name="arch" type="xml"><data>
      <xpath expr="//field[@name='gone']" position="after"><field name="y"/></xpath>
    </data></field>
  </record>
</odoo>
XML
cat > "$mod/static/src/xml/t.xml" <<'XML'
<templates>
  <t t-name="acme.Good" t-inherit="base.Widget" t-inherit-mode="extension">
    <xpath expr="//span[@class='lbl']" position="after"><b/></xpath>
  </t>
  <t t-name="acme.Bad" t-inherit="base.Widget" t-inherit-mode="extension">
    <xpath expr="//span[@class='nope']" position="after"><b/></xpath>
  </t>
  <t t-name="acme.Primary" t-inherit="base.Widget" t-inherit-mode="primary">
    <xpath expr="//span[@class='nope']" position="after"><b/></xpath>
  </t>
</templates>
XML
cat > "$wt/other/o.xml" <<'XML'
<odoo><record id="o_bad" model="ir.ui.view">
  <field name="inherit_id" ref="base.view_form"/>
  <field name="arch" type="xml"><data><xpath expr="//field[@name='zz']" position="after"><b/></xpath></data></field>
</record></odoo>
XML

run() { PYTHONPATH="$work/stub" python3 "$SUT" "$@" 2>"$work/err"; }

out="$(run --worktree "$wt" --series 19.0 --source "$work/src" --modules acme)"; rc=$?
expect "rc miss" "$rc" 1
expect "checked" "$(jq_py "$out" "d['checked']")" 4
expect "missing" "$(jq_py "$out" "d['missing']")" 2
expect "series" "$(jq_py "$out" "d['series']")" 19.0
expect "bad names" "$(jq_py "$out" "sorted(r['view'] for r in d['rows'] if r['status']=='missing')")" "['acme.Bad', 'acme.view_bad']"
expect "ok names" "$(jq_py "$out" "sorted(r['view'] for r in d['rows'] if r['status']=='ok')")" "['acme.Good', 'acme.view_ok']"
case "$(jq_py "$out" "[r['error'] for r in d['rows'] if r['status']=='missing'][0]")" in
  *"cannot be located"*) expect "error text" yes yes ;; *) expect "error text" no yes ;; esac

out="$(run --worktree "$wt" --series 19.0 --source "$work/src")"; rc=$?
expect "no filter checked" "$(jq_py "$out" "d['checked']")" 5

out="$(run --worktree "$wt" --series 19.0 --source "$work/src" --modules other)"
expect "filter other" "$(jq_py "$out" "[r['module'] for r in d['rows']]")" "['other']"

rm -rf "$mod/static" "$wt/other"; rm "$mod/views/v.xml"
cat > "$mod/views/v.xml" <<'XML'
<odoo><record id="view_ok" model="ir.ui.view">
  <field name="inherit_id" ref="base.view_form"/>
  <field name="arch" type="xml"><data><xpath expr="//field[@name='name']" position="after"><b/></xpath></data></field>
</record></odoo>
XML
out="$(run --worktree "$wt" --series 19.0 --source "$work/src")"; rc=$?
expect "all ok rc" "$rc" 0

python3 "$SUT" --worktree "$wt" --series 19.0 --source "$work/src" >/dev/null 2>"$work/err"; rc=$?
expect "no odoo rc" "$rc" 2
expect "no odoo hint" "$(grep -c 'target-series devcontainer' "$work/err")" 1

run --worktree "$wt" --series 19.0 --source "$work/nodir" >/dev/null; rc=$?
expect "bad source rc" "$rc" 2

echo "xpath-check.test.sh: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
