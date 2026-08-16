#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed, read-only release closure verifier.

This command never accepts caller-supplied release strings. It authenticates through
an absolute `gh` CLI, derives the GitHub repository from `origin`, and reads the
Release, Latest Release, release assets, and fleet-adoption record from the GitHub
API. It never tags, pushes, installs, or mutates runtime state.
"""
from __future__ import annotations
import argparse, base64, json, os
from pathlib import Path
import re, subprocess
from typing import Any

VERSION=re.compile(r"^\d+\.\d+\.\d+$"); SHA=re.compile(r"^[0-9a-f]{40}$"); DIGEST=re.compile(r"^sha256:[0-9a-f]{64}$")
ADOPTION_FILES=("README.md","docs/CURRENT-DEFAULTS.md","install.sh","scripts/coordinator-registry.py","scripts/coordinator-reconcile.py","scripts/coordinator-status.py","scripts/recovery-admission.py","scripts/lane-admission.py","scripts/worker-lease.py","scripts/observable-job.py","skills/coordinator-lifecycle-protocol/SKILL.md","skills/worker-completion-protocol/SKILL.md","skills/self-healing-controller/SKILL.md")

def run(command:list[str], cwd:Path|None=None)->tuple[int,str]:
 try:
  p=subprocess.run(command,cwd=cwd,text=True,capture_output=True,timeout=60);return p.returncode,p.stdout.strip()
 except (OSError,subprocess.SubprocessError): return 127,""
def git(repo:Path,*args:str)->tuple[int,str]: return run(["git","-C",str(repo),*args])
def gh()->list[str]|None:
 raw=os.environ.get("CRAFT_GH_CLI",""); parts=raw.split()
 return parts if parts and Path(parts[0]).is_absolute() and Path(parts[0]).is_file() else None
def api(cli:list[str], endpoint:str)->dict[str,Any]|None:
 code,out=run([*cli,"api",endpoint]);
 try: return json.loads(out) if code==0 and isinstance(json.loads(out),dict) else None
 except Exception:return None
def repo_identity(repo:Path)->str|None:
 code,url=git(repo,"remote","get-url","origin")
 if code:return None
 match=re.search(r"github\.com[/:]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$",url)
 return match.group(1) if match else None
def manifest_errors(repo:Path)->list[str]:
 p=repo/"manifest.sha256"; code,_=run(["shasum","-a","256","-c",str(p)],repo)
 return [] if p.is_file() and code==0 else ["manifest-mismatch"]
def sha256(repo:Path,rel:str)->str|None:
 p=repo/rel
 if not p.is_file():return None
 code,out=run(["shasum","-a","256",str(p)])
 return out.split()[0] if code==0 and out else None
def installer_errors(repo:Path,version:str)->list[str]:
 p=repo/"install.sh"
 if not p.is_file() or f"v{version}" not in p.read_text(errors="ignore"):return ["installer-version-mismatch"]
 code,out=run(["zsh",str(p)],repo)
 return [] if code==0 and "No files changed." in out else ["installer-dry-run-not-proven"]
def adoption_errors(repo:Path,version:str)->list[str]:
 missing=[r for r in ADOPTION_FILES if not (repo/r).is_file() or version not in (repo/r).read_text(errors="ignore")]
 return ["version-adoption-missing:"+",".join(missing)] if missing else []
def asset_digest(release:dict[str,Any],name:str)->str|None:
 for a in release.get("assets") or []:
  if isinstance(a,dict) and a.get("name")==name and isinstance(a.get("digest"),str): return a["digest"]
 return None
def fleet_adoption(cli:list[str],identity:str,tag:str,version:str,tag_sha:str)->list[str]:
 row=api(cli,f"repos/{identity}/contents/.craft-protocol/adoptions/{tag}.json?ref={tag}")
 if not row or row.get("type")!="file" or not isinstance(row.get("content"),str):return ["fleet-adoption-record-missing"]
 try: value=json.loads(base64.b64decode(row["content"]).decode())
 except Exception:return ["fleet-adoption-record-invalid"]
 required={"schemaVersion":1,"version":version,"tag":tag,"commit":tag_sha,"state":"adopted"}
 return [] if all(value.get(k)==v for k,v in required.items()) and isinstance(value.get("adoptedAt"),str) and value["adoptedAt"] else ["fleet-adoption-record-mismatch"]
def verify(repo:Path,version:str)->dict[str,Any]:
 errors:list[str]=[]; tag=f"v{version}"; identity=repo_identity(repo); cli=gh()
 if not VERSION.fullmatch(version):return {"closed":False,"version":version,"errors":["version-invalid"]}
 if not identity:errors.append("github-origin-identity-unreadable")
 if not cli:errors.append("github-auth-cli-unavailable")
 elif run([*cli,"auth","status"])[0]!=0:errors.append("github-auth-unavailable")
 _,main=git(repo,"rev-parse","refs/remotes/origin/main")
 if not SHA.fullmatch(main): errors.append("remote-main-unreadable");main=""
 typ=git(repo,"cat-file","-t",f"refs/tags/{tag}")[1]
 _,tag_sha=git(repo,"rev-parse",f"{tag}^{{commit}}")
 if typ!="tag" or not SHA.fullmatch(tag_sha):errors.append("annotated-tag-missing-or-unreadable");tag_sha=""
 elif tag_sha!=main:errors.append("tag-does-not-peel-exactly-to-remote-main")
 errors+=manifest_errors(repo)+installer_errors(repo,version)+adoption_errors(repo,version)
 release=latest=None
 if cli and identity and tag_sha:
  release=api(cli,f"repos/{identity}/releases/tags/{tag}"); latest=api(cli,f"repos/{identity}/releases/latest")
  if not release:errors.append("github-release-object-missing")
  else:
   if release.get("tag_name")!=tag or release.get("draft") is not False or release.get("prerelease") is not False:errors.append("github-release-object-mismatch")
   if not isinstance(release.get("published_at"),str) or not release["published_at"]:errors.append("github-release-freshness-missing")
   if not latest or latest.get("id")!=release.get("id") or latest.get("tag_name")!=tag:errors.append("github-release-not-latest")
   if release.get("target_commitish") not in {tag_sha,"main"}:errors.append("github-release-target-mismatch")
   for name in ("manifest.sha256","install.sh"):
    digest=asset_digest(release,name); local=sha256(repo,name)
    if not local or digest!=f"sha256:{local}":errors.append(f"github-release-asset-hash-mismatch:{name}")
  errors+=fleet_adoption(cli,identity,tag,version,tag_sha)
 elif tag_sha:errors.append("github-release-uncheckable-without-auth")
 return {"closed":not errors,"version":version,"tag":tag,"repository":identity,"remoteMainSha":main or None,"tagSha":tag_sha or None,"errors":sorted(set(errors))}
def main()->int:
 p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest="command",required=True);v=s.add_parser("verify");v.add_argument("--repo",default=str(Path(__file__).resolve().parents[1]));v.add_argument("--version",required=True);a=p.parse_args()
 result=verify(Path(a.repo).expanduser().resolve(),a.version);print(json.dumps(result,indent=2));return 0 if result["closed"] else 2
if __name__=="__main__":raise SystemExit(main())
