# Swarm identity

**My identity: `<your-name>`**

*This file is a template. It is the shared, name-free copy; your named copy lives in your state
directory, `$SP/SWARM-IDENTITY.md`. Do not edit the name in the kit checkout — it is re-synced by
`kit_update.py` on every session start. Edit your copy in `$SP`.*

## 0. Claim your name, once

If you are reading this and line 3 still reads `<your-name>`, you are the first agent here. Do this
before you reserve anything:

1. **Pick a name.** Short, lowercase, memorable, unlikely to collide. Not `sable`, `sfh`,
   `shubshub` or anything else already in the table in §5.
2. **Copy this file into your state directory** and fill it in there:

        cp docs/SWARM-IDENTITY.md "$SP/SWARM-IDENTITY.md"

   `SP` is what `python kitpaths.py state` prints, `../dqix-kit-state` by default. Hard rule 18 puts
   state outside the checkout for exactly this reason: `kit_update.py` will overwrite anything you
   write into `$KIT`, and no git command run in the checkout can reach `$SP`.
3. **Write your name into line 3 of your copy**, and add a row for yourself to §5.
4. **Re-use that name forever.** Not per session — continuity is the entire point. A name that
   changes is indistinguishable from an unstamped agent.

If a name is already in §5 and is not yours, you are not the first: skip §0, read §2, and check
whether it is yours before reusing it.

## 1. Why this exists

There are **multiple independent swarms** running on different machines and networks, all
authenticating as the **same GitHub account**. Consequences:

- `gh issue list --author <you>` returns issues belonging to *other* agents, not yours.
- Two agents can reserve the **same addresses** without either being able to tell, because neither
  can distinguish the other's issue from its own.
- `git status` on a shared branch shows commits you did not make.
- A kit issue you did not open may be the thing currently holding the address you want.

None of this is a GitHub bug and none of it can be fixed by configuration. It is fixed by **every
agent declaring an identity in the shared namespace and stamping it on its artifacts.**

## 2. The protocol

**Pick a name.** See §0. Short, lowercase, memorable, unlikely to collide. Write it into your copy,
into `OPEN_WORK.md` in `$SP`, and into your own session notes, and re-use it forever.

**Stamp it on every reservation.** Title and body:

```
[<name>] Wave 35: thirty-two free main functions, ranked by sibling proximity
```

First line of the body:

```
Agent: <name> (machine <hostname>, wave 35 started <date>)
```

**Find your own issues:**

```bash
gh issue list --repo ZevyaDev/dqix-decomp-kit --state open --search "in:title [<name>]"
gh pr     list --repo ZevyaDev/dqix-decomp      --state open --search "[<name>] in:title"
```

**Find everyone else's, and respect them:**

```bash
gh issue list --repo ZevyaDev/dqix-decomp-kit --state open --limit 100
```

That second command is **mandatory before every reservation** (hard rule 20), and it is not optional
just because your own list is empty. Another agent's open issue reserves its addresses exactly as
firmly as yours does.

**Never edit or close an issue that is not stamped with your name.** If one of your addresses is
held by another agent, leave it alone and pick different addresses. If you need theirs, comment on
the issue and ask — do not close it "because it looks stale". A closed issue whose pull request never
opened is free again, which is the only clean release.

## 3. Reservation protocol, in full

Before opening a reservation:

1. `pick_wave.py` — propose the batch.
2. `audit_batch.py` — verify every address is free.
3. `gh issue list --state open --limit 100` — **read all of them**, yours and other agents'. Extract
   every address from every open issue body and reject any overlap.
4. `gh pr list --repo ZevyaDev/dqix-decomp --state open` — an open pull request's changed files are
   reserved too. A pull request from another agent claiming `func_020b752c.cpp` reserves 020b752c.
5. Also check *upstream* `decomp-matching` again — another agent may have merged a pull request that
   matched one of your addresses minutes ago.

On close:

6. Comment `PR: ZevyaDev/dqix-decomp#N`, close the issue, and say plainly which addresses did **not**
   land and why, so the next agent does not re-buy them.

## 4. Working on a shared branch

All swarms push to `decomp-matching` on the fork. That is intentional — upstream is one branch and
the decomp checkout fast-forwards through it.

- **Never** `git reset --hard`, `git rebase` away, or force-push another agent's commits.
- Before landing, `git fetch` and check what arrived. If upstream moved, merge it
  (`git merge <upstream>/decomp-matching`) and expect a **textual conflict in
  `config/usa/arm9/delinks.txt`** — both sides append matched functions to the same region. Use the
  kit's `resolve_delinks.py`, which unions both sides.
- If a merge brings `.github/workflows/` changes, your token cannot push them (`workflow` scope, and
  `gh auth refresh` is interactive). Keep your branch's own copy of the workflow file so the push
  never modifies it — a file unchanged on your side produces no merge diff.

## 5. Who is active

*Unnamed agents: add a row here the day you claim a name in §0. The table lives in everyone's copy,
so it drifts — the live source is the open issue list, not this table.*

| identity | machine | notes |
|---|---|---|
| *`<your-name>`* | *`<hostname>`* | *first session, wave 1. No open reservation.* |

An issue with no `[<name>]` stamp is **not** an abandoned one. It belongs to whichever swarm is
running unstamped, and it reserves its addresses exactly as firmly as a stamped one does.

Adding yourself to this table is part of picking a name. If the table is empty of anyone but you,
you still do not assume you have the machine to yourself — check the live issue list.

## 6. What this does not solve

GitHub cannot make two agents hold separate accounts from one login. This protocol is **convention**,
not enforcement. It works because agents are cooperating; it fails the moment one ignores it. The one
thing that makes failure detectable is reading the open issue list before reserving — do that even
if you trust the convention, because someone else's bug then costs only a re-pick rather than a
duplicated wave.