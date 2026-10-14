export const meta = {
  name: 'dqix-evolve',
  description: 'Evolutionary search on one DQIX function: a scored population, directed and random mutations, crossover, niche-preserving selection, until MATCH or plateau',
  whenToUse: 'One function at a known residue. Keeps several differently-wrong candidates alive, mutates them with planned and random levers, and branches from non-best files.',
  phases: [
    { title: 'Score', detail: 'pad/evo_score.py on every new file' },
    { title: 'Plan', detail: 'directed levers for the differing sites' },
    { title: 'Mutate', detail: 'directed, random and crossover children' },
  ],
}

// args: { mod, addr, base: file | [files], board, pop, width, explore, cross, maxGens, plateau, seed, sites: [str], notes, seedLevers: [{key, prompt, parent?}], force, sp, repo }
const A = args || {}
const MOD = A.mod
const ADDR = A.addr
const BOARD = A.board
const POP = A.pop || 5
const WIDTH = A.width || 2
const EXPLORE = A.explore === undefined ? 0 : A.explore
const FORCE = !!A.force
const CROSS = A.cross === undefined ? 1 : A.cross
const MAXGENS = A.maxGens || 6
const PLATEAU = A.plateau || 3
const NOTES = A.notes || ''
const SITES = (Array.isArray(A.sites) && A.sites.length ? A.sites : ['the differing sites']).concat(['anywhere in the function'])
const SEED = Array.isArray(A.seedLevers) ? A.seedLevers : []
const BASES = Array.isArray(A.base) ? A.base : [A.base]
const PATHS_SCHEMA = {
  type: 'object',
  properties: { kit: { type: 'string' }, sp: { type: 'string' }, repo: { type: 'string' } },
  required: ['kit', 'sp', 'repo'],
}
async function kitPaths() {
  if (A.kit && A.sp && A.repo) return { kit: A.kit, sp: A.sp, repo: A.repo }
  const root = A.kit || '${DQIX_KIT:-.}'
  const r = await agent(
    `Run exactly this one command and nothing else:
cd "${root}" && python -c "import kitpaths as k; print(k.KIT); print(k.SP); print(k.REPO)"
Return the first line it prints as kit, the second as sp and the third as repo, verbatim. Do not edit anything.`,
    { label: 'paths', schema: PATHS_SCHEMA, effort: 'low' })
  if (!r) throw new Error('could not resolve the kit paths; pass args.kit, args.sp and args.repo')
  return { kit: A.kit || r.kit, sp: A.sp || r.sp, repo: A.repo || r.repo }
}
const PATHS = await kitPaths()
const KIT = PATHS.kit
const SP = PATHS.sp
const REPO = PATHS.repo
const SCORER = `cd ${REPO} && python ${KIT}/pad/evo_score.py ${MOD} ${ADDR}`
const CAPCHECK = `python ${KIT}/evocap.py ${ADDR} --reserve ${MOD}`
const CASEDIFF = ADDR === '02061c04' ? `     and     python ${KIT}/pad/caseresidue.py <file> --all` : ''

let seed = ((A.seed || 20260910) >>> 0) || 1
function rnd() { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return seed / 4294967296 }
function pick(arr) { return arr[Math.floor(rnd() * arr.length)] }

const OPS = [
  'ACCESSOR: move a raw pointer/offset expression into a small static inline accessor, split one accessor into two levels, or inline one back',
  'LOCALS: introduce, remove, merge or split a named local; change where it is declared or first assigned; change its width or signedness',
  'EXPRESSION: rewrite an expression tree -- operand order, compound vs plain assignment, cast placement, shift vs multiply/divide, signed vs unsigned constants',
  'CONTROL: rewrite control flow -- if/else vs ternary vs early return, inverted condition, loop form, where a shared tail sits',
  'STRUCTS: model the memory as a typed struct or class with members or member functions instead of casts, or the reverse',
  'INLINE: wrap a statement group in a small static inline helper in SDK style (like FX_Mul), change a helper\'s parameters or return type, or unwrap a helper',
  'COUPLING: change code OUTSIDE the differing sites, in a case body that must stay byte-identical -- a residue can depend on other code in the same function',
  'STORAGE: change how a global the function touches is DEFINED, not how it is used -- a static inside the function, or inside an inline accessor (inline T& GetT() { static T s = {...}; return s; }) when other functions reference it, at its TRUE size (the gap to the next symbol in symbols.txt, never the offsets this function touches) and with the ROM\'s initial bytes; or back to extern',
  'REFERENCE: do not invent a form -- find the ROM\'s pairing in a committed matching source (findshape/findladder/shapecat, or grep src for the mnemonics), in a reference decomp under refs/, or on the web when the code looks like library/SDK/runtime code, and copy the C form it uses',
]

const RULES = `
BOARD ${BOARD} lists everything already tried. Read \`tail -60\` of it first -- never the whole file, it is large.
Append ONE line per scored file:  echo "EVO <your label> <fitness> <sig> <the edit in a few words>" >> ${BOARD}
SCORE:   ${SCORER} <file> [<file> ...]     one JSON line per file; fitness 0 = MATCH; sig names each differing site
INSPECT: cd ${REPO} && WDIFF_CTX=2 python ${KIT}/wdiff.py ${MOD} ${ADDR} <file>${CASEDIFF}
The source must be something the original developers could have written: NO #pragma of any kind, no always_inline or noinline
attributes, no compiler overrides, no fake extra call arguments, no assembly. The scorer rejects such files.
AND WHEN A SITE RESISTS EVERY FORM, THE RESIDUE IS POINTING AT CODE SOMEBODY INVENTED. Read the whole file for constructs
the developers could not have written -- a template or macro system for field access, several spellings of the same access
picked per site for their codegen, a variable reused for two unrelated things, a store nested inside another store's operand,
one object cast to several struct types. Each of those changes what mwcc knows about ALIASING and about common
subexpressions, which is what decides scheduling and register choice. Replace them with the natural form (one typed struct
with named members, accessed as self->field) and re-measure, accepting a worse number to be in the right basin. Do this
BEFORE the third attempt at one site, not after the seventh.
MINE BEFORE YOU GUESS -- the ROM's own form is usually written down somewhere already, and reading it
costs a fraction of a blind variant sweep:
* THE CORPUS. Committed matching sources are proof of what mwcc emits for a C form. Find ones whose
  ROM code has the pairing you cannot produce: \`python ${KIT}/pad/findshape.py [--twonode]\`,
  \`pad/findladder.py\`, \`pad/shapecat.py\`, or a plain grep over ${REPO}/src for the mnemonics or the
  constant. Map a source to its address by its \`// USA:\` comment as well as by its filename, or you
  will miss every semantically-named file. Then read that file's C and copy the FORM, not the text.
* REFERENCE DECOMPS. \`${SP}/refs\` holds verified NDS decomps. Their C is evidence; a splitter
  dump (\`arm_func_start\`) is NOT -- it is the same assembly you already have.
* THE WEB, when the function looks like library, SDK or runtime code rather than game logic: a
  distinctive identifier, string or constant often finds the original C. WebSearch and WebFetch, and
  \`gh search code "<identifier>"\` for other decomps. That is how 020042a8 was identified as MSL
  \`__strtold\` from \`hex_scan_state\`. A decomp.me scratch fetch needs a browser User-Agent and a
  Referer header.
Say in your report which of these you searched and what you found, so the next generation does not
repeat it.
PROBE BEFORE YOU SWEEP. Compiling the whole function to test one construct costs a minute; a 10-line
probe costs a second:  python ${KIT}/pad/probe_cc.py <probe.cpp> --bytes  prints the codegen of a
scratch file built with the project compiler and flags. Reproduce the ROM's instruction pairing in a
probe, sweep twenty forms there in the time one whole-function compile takes, then apply only the
winners to the real file. A probe is NOT faithful on its own -- it omits the surrounding code that
decides scheduling and register pressure -- so every probe answer is a candidate, and the scorer on
the real function is what settles it.
Work only in your own directory (create it). Never edit your parent file, src/, attempts/ or another agent's files. Never commit.
Score several variants in ONE scorer call. Keep the best-scoring file you produced even when it is WORSE than your parent:
a file that is wrong at a different site can be the branch that leads to MATCH. Report that file.`

const SCORE_SCHEMA = {
  type: 'object',
  properties: {
    results: { type: 'array', items: { type: 'object', properties: {
      file: { type: 'string' }, fitness: { type: 'integer' }, sig: { type: 'string' },
      verdict: { type: 'string' }, match: { type: 'boolean' } },
      required: ['file', 'fitness', 'sig', 'match'] } },
    cap: { type: 'string', description: 'the last line evocap.py printed, verbatim' },
  },
  required: ['results'],
}
const PLAN_SCHEMA = {
  type: 'object',
  properties: {
    levers: { type: 'array', items: { type: 'object', properties: {
      key: { type: 'string' }, parent: { type: 'string' }, prompt: { type: 'string' } },
      required: ['key', 'parent', 'prompt'] } },
    diagnosis: { type: 'string' },
  },
  required: ['levers', 'diagnosis'],
}
const CHILD_SCHEMA = {
  type: 'object',
  properties: {
    lever: { type: 'string' },
    file: { type: 'string', description: 'absolute path of the best file you produced (kept even if worse than the parent)' },
    fitness: { type: 'integer', description: 'its fitness from the scorer; 99999 if nothing compiled' },
    sig: { type: 'string' },
    matched: { type: 'boolean' },
    what_changed: { type: 'string', description: 'the exact source edit relative to the parent' },
    ruled_out: { type: 'string' },
  },
  required: ['lever', 'file', 'fitness', 'what_changed', 'ruled_out'],
}

let pop = []
const history = []
let winner = null

function describe(p) { return `${p.file}  fitness ${p.fitness}  sig ${p.sig}` }

function select() {
  const byFile = new Map()
  for (const p of pop) if (!byFile.has(p.file) || byFile.get(p.file).fitness > p.fitness) byFile.set(p.file, p)
  const all = [...byFile.values()].filter(p => p.fitness < 99999).sort((a, b) => a.fitness - b.fitness)
  const keep = []
  const sigs = new Set()
  for (const p of all) {
    if (keep.length >= POP) break
    if (!sigs.has(p.sig)) { keep.push(p); sigs.add(p.sig) }
  }
  for (const p of all) {
    if (keep.length >= POP) break
    if (!keep.includes(p)) keep.push(p)
  }
  const dropped = all.length - keep.length
  pop = keep.sort((a, b) => a.fitness - b.fitness)
  return dropped
}

function tournament() {
  if (pop.length < 2) return pop[0]
  const a = pick(pop), b = pick(pop)
  const better = a.fitness <= b.fitness ? a : b
  const worse = better === a ? b : a
  return rnd() < 0.6 ? better : worse
}

let capLine = ''

async function scoreFiles(files, gen) {
  const uniq = [...new Set(files.filter(f => typeof f === 'string' && f.length))]
  if (!uniq.length) return []
  const r = await agent(
    `Run exactly this one command and nothing else:
${SCORER} ${uniq.join(' ')}
Return every JSON line it prints as one entry of results (file, fitness, sig, verdict, match), verbatim. Then append one line per file:
echo "EVO SCORED g${gen} <file> <fitness> <sig>" >> ${BOARD}
${FORCE ? '' : `Then run \`${CAPCHECK}\` and return the last line it prints as cap, verbatim.\n`}Do not edit anything else.`,
    { label: `score-g${gen}`, phase: 'Score', schema: SCORE_SCHEMA, effort: 'low' })
  capLine = (r && r.cap) || ''
  return ((r && r.results) || []).map(x => ({ file: x.file, fitness: x.fitness, sig: x.sig, verdict: x.verdict || '', match: !!x.match, gen }))
}

function capped() {
  if (FORCE || !capLine.startsWith('EVOCAP STOP')) return false
  log(`${capLine} -- stopping; hand this function to a different technique instead of relaunching evolve`)
  return true
}

function childPrompt(job, gen) {
  const dir = `${SP}/handwork/evo/${ADDR}/g${gen}_${job.key}`
  if (job.kind === 'cross') {
    return `TARGET ${MOD} ${ADDR}. You are the CROSSOVER worker for generation ${gen}. Two population members are wrong at different sites:
A: ${describe(job.a)}
B: ${describe(job.b)}
In ${dir}, build files that combine them: diff -u A B, apply subsets of B's hunks to a copy of A and of A's hunks to a copy of B, try the combinations that touch different sites. Score them in one call and keep the best.
${RULES}`
  }
  if (job.kind === 'exp') {
    return `TARGET ${MOD} ${ADDR}. You are a RANDOM MUTATION worker for generation ${gen}: explore, do not polish. PARENT: ${describe(job.parent)}
OPERATOR: ${job.op}
SITE: ${job.site}${job.site === 'anywhere in the function' ? ' -- pick one case body or statement group at random (e.g. python -c "import random; print(random.choice([...]))" over the case labels) and say which' : ''}
Make 4 to 8 different concrete rewrites of that kind at that site in copies of the parent under ${dir}, each a separate file. Prefer rewrites the board has never logged. Score all of them in one call and keep the best by fitness, even if it is worse than the parent.
${RULES}`
  }
  return `TARGET ${MOD} ${ADDR}. PARENT: ${describe(job.parent)}. Copy it into ${dir} and work on copies; beat its fitness, MATCH (fitness 0) is the goal.
YOUR LEVER (${job.key}): ${job.prompt}
${RULES}`
}

phase('Score')
pop = await scoreFiles(BASES, 0)
select()
if (!pop.length) {
  log('No base file scored -- stopping')
} else {
  log(`Gen 0 population: ${pop.map(p => `${p.fitness} [${p.sig}]`).join(', ')}`)
  history.push({ gen: 0, population: pop.map(p => ({ file: p.file, fitness: p.fitness, sig: p.sig })) })
}
if (pop.length && pop[0].match) winner = pop[0]
let stopped = !winner && capped()

let best = pop[0]
let flat = 0
let gen = 0
for (gen = 1; gen <= MAXGENS && !winner && !stopped && pop.length; gen++) {
  let levers
  if (gen === 1 && SEED.length) {
    levers = SEED.map(s => ({ key: s.key, parent: s.parent || pop[0].file, prompt: s.prompt }))
  } else {
    phase('Plan')
    const plan = await agent(
      `You plan generation ${gen} of an evolutionary search on ${MOD}:${ADDR}. Population, best first:
${pop.map(describe).join('\n')}
You have AT MOST SIX tool calls, then you answer: \`tail -80 ${BOARD}\`; \`${SCORER} <best file>\` or \`python ${KIT}/pad/caseresidue.py <file> --all\`; one \`sed -n\` of the source at a differing site; one more inspection; and up to two calls that MINE for the ROM's form rather than guess it -- \`python ${KIT}/pad/findshape.py\`, a grep over ${REPO}/src for the mnemonics or constant at the differing site, a read of a reference decomp under ${SP}/refs, or a WebSearch / \`gh search code\` when the code looks like library, SDK or runtime code. Do not open other agents' files or logs.
At least one of your levers should carry evidence from a matching source, a reference decomp or the web -- name the file or URL in its prompt -- unless you searched and found nothing, in which case say so.
Propose EXACTLY ${WIDTH} levers, each {key, parent, prompt}: parent must be one of the population files above; the prompt names the differing site, what the ROM does there versus ours, and concrete untried source edits. Levers must differ from each other and from the board. If the population has more than one file, put at least one lever on a file that is not the best.
Context: ${NOTES}`,
      { label: `plan-g${gen}`, phase: 'Plan', schema: PLAN_SCHEMA })
    levers = ((plan && plan.levers) || []).slice(0, WIDTH)
  }
  const jobs = []
  for (const l of levers) {
    const parent = pop.find(p => p.file === l.parent) || pop[0]
    jobs.push({ kind: 'dir', key: l.key.replace(/[^\w-]/g, '_').slice(0, 40), parent, prompt: l.prompt })
  }
  for (let i = 0; i < EXPLORE; i++) {
    jobs.push({ kind: 'exp', key: `explore${i}`, parent: tournament(), op: pick(OPS), site: pick(SITES) })
  }
  const other = pop.find(p => p.sig !== pop[0].sig)
  if (CROSS && other) jobs.push({ kind: 'cross', key: 'cross', a: pop[0], b: other })
  log(`Gen ${gen}: ${jobs.map(j => j.kind === 'exp' ? `explore(${j.op.split(':')[0]} @ ${j.site}) on ${j.parent.fitness}` : j.kind === 'cross' ? `cross ${j.a.fitness}x${j.b.fitness}` : `${j.key} on ${j.parent.fitness}`).join('; ')}`)

  phase('Mutate')
  const children = (await parallel(jobs.map(j => () => agent(childPrompt(j, gen),
    { label: `g${gen}:${j.key}`, phase: 'Mutate', schema: CHILD_SCHEMA })))).filter(Boolean)

  const scored = await scoreFiles(children.map(c => c.file), gen)
  const hit = scored.find(s => s.match || s.fitness === 0)
  if (hit) { winner = hit; pop.unshift(hit); break }
  pop.push(...scored)
  if (capped()) { stopped = true; select(); break }
  const dropped = select()
  history.push({ gen, children: children.map(c => ({ lever: c.lever, file: c.file, fitness: c.fitness, what: c.what_changed })),
    population: pop.map(p => ({ file: p.file, fitness: p.fitness, sig: p.sig })) })
  log(`Gen ${gen}: scored ${scored.length}, population ${pop.map(p => `${p.fitness} [${p.sig}]`).join(', ')}${dropped ? ` (${dropped} dropped)` : ''}`)
  if (pop[0].fitness < best.fitness) {
    best = pop[0]
    flat = 0
    log(`Gen ${gen}: new best ${best.fitness} -- ${best.file}`)
  } else {
    flat++
    log(`Gen ${gen}: best still ${best.fitness} (${flat}/${PLATEAU})`)
    if (flat >= PLATEAU) break
  }
}

if (winner) log(`MATCH -- ${winner.file}`)
return { matched: !!winner, capped: stopped ? capLine : '', best: winner || pop[0], generations: gen, population: pop, history }
