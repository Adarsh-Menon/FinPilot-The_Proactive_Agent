import React, {useEffect, useRef, useState} from 'react';
import {ArrowDownLeft, ArrowUpRight, ArrowRight, BarChart3, Check, ChevronRight, Compass, FileUp, LayoutDashboard, List, LogOut, Plus, RefreshCw, Settings2, ShieldCheck, Sparkles, Wallet, X, Activity, MessageCircle, Send, Target, Trash2, TriangleAlert, TrendingUp, Gift, Copy, ThumbsUp, ThumbsDown, Landmark, LifeBuoy, Award, PiggyBank, ExternalLink, Lock, Newspaper, LineChart, Mic, MicOff, Volume2, VolumeX} from 'lucide-react';

/**
 * Format minor units as currency, e.g. 2500000 -> "₹25,000".
 *
 * @param value amount in minor units (null shows "Not provided")
 * @param currency ISO currency code, e.g. INR
 * @returns Formatted text.
 * Used in: Everywhere amounts are shown.
 */
const money = (value, currency='INR') => value == null ? 'Not provided' : new Intl.NumberFormat('en', {style:'currency', currency, maximumFractionDigits:0}).format(value / 100);
const today = '2026-10-18';
const initialProfile = {currency:'INR', timezone:'Asia/Dubai', as_of:today, coverage_start:'2026-08-20', coverage_confirmed:false, opening_balance_minor:null, reserve_minor:null, scheduled_flows:[]};
/**
 * A Date as an ISO day string (YYYY-MM-DD).
 *
 * @param date Date object
 * @returns ISO date string.
 * Used in: Date maths in charts and filters.
 */
const iso = date => date.toISOString().slice(0,10);
/**
 * A typed rupee amount as minor units (₹12.50 -> 1250).
 *
 * @param value input text
 * @returns Integer minor units, or null when empty.
 * Used in: Every amount input.
 */
const amount = value => value === '' ? null : Math.round(Number(value)*100);
const unresolvedReasons = {unavailable:'AI could not categorize this one.', needs_clarification:'AI could not tell from the description alone.'};
/**
 * Why a transaction has its category, for the tooltip and fix dialog.
 *
 * @param t transaction
 * @returns Plain sentence.
 * Used in: Transactions table and the Fix this category dialog.
 */
const categoryReason = t => t.category_source==='owner'?'Set by you.':t.category_source==='model'?(t.category_reason||'Categorized by AI.'):t.category_source==='merchant_rule'?(t.category_reason||'Matches one of your labels.'):unresolvedReasons[t.category_status]||'Not categorized yet.';
/**
 * AI generations already spent on a transaction (older rows are inferred).
 *
 * @param t transaction
 * @returns Number.
 * Used in: Category history.
 */
const aiAttempts = t => t.category_ai_attempts ?? ((t.category_source==='model'||['needs_clarification','unavailable'].includes(t.category_status))?1:0);
const opsMeaning = {classified:'Categorized by the model', owner:'Labeled by the customer', needs_clarification:'Model abstained', unavailable:'Model call failed or output rejected', paused:'Arrived while AI policy was paused', not_configured:'Arrived before AI credentials were configured'};
/**
 * What the owner's context still lacks: cash, upcoming income, a reserve, confirmed history.
 *
 * @param p profile or null
 * @returns List of plain phrases.
 * Used in: The "Not sure what to fill in?" banner and Fina's questions prompt.
 */
const missingFacts = p => p ? [...(p.opening_balance_minor==null?['your available cash']:[]),...(!p.scheduled_flows.some(f=>f.amount_minor>0&&f.date>p.as_of)?['upcoming income']:[]),...(p.reserve_minor==null?['a reserve']:[]),...(!p.coverage_confirmed?['confirmed history']:[])] : [];
const stepNames = {analyzer:'Behavioural pattern agent', forecaster:'Forecaster', numeric_checks:'Numeric checks'};
const plannedAgents = [];

/**
 * The FinPilot logo mark as inline SVG; animated, it is the loading indicator.
 *
 * @param props.animated draw the line and pop the dot and ring on a loop
 * @returns SVG element.
 * Used in: Every loading state and the import progress note.
 */
function FinMark({animated=false}){
  return <svg className={`fin-mark ${animated?'animated':''}`} viewBox="0 0 1024 1024" role={animated?'img':undefined} aria-label={animated?'Loading':undefined} aria-hidden={animated?undefined:'true'}>
    <defs><linearGradient id="fin-mark-g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#76edc6"/><stop offset="1" stopColor="#3ccf9e"/></linearGradient></defs>
    <circle cx="512" cy="512" r="512" fill="url(#fin-mark-g)"/>
    <polyline className="fin-line" points="215,682 378,522 500,592 642,440" fill="none" stroke="#0a1529" strokeWidth="78" strokeLinecap="round" strokeLinejoin="round" pathLength="100"/>
    <circle className="fin-dot" cx="727" cy="355" r="38" fill="#0a1529"/>
    <circle className="fin-ring" cx="824" cy="255" r="53" fill="none" stroke="#0a1529" strokeWidth="44"/>
  </svg>;
}

// Spending by category from the owner's own expenses: one series, so one hue and no legend.
const spendPeriods=[[30,'Last 30 days'],[60,'Last 60 days'],[90,'Last 90 days'],[0,'All history']];
/**
 * A category name with a capital first letter ("uncategorized" -> "Uncategorized").
 *
 * @param c category
 * @returns Text.
 * Used in: Where your money goes.
 */
const titleCase=c=>c==='uncategorized'?'Uncategorized':c.charAt(0).toUpperCase()+c.slice(1);
/**
 * Overview's "Where your money goes": most and least expensive categories and a sorted bar per category.
 *
 * @param props.txs the owner's transactions
 * @param props.asOf balance date
 * @param props.currency ISO currency code, e.g. INR
 * @param props.onPick called with {categories, label, from, to, period} when a bar is clicked
 * @returns Panel element.
 * Used in: Overview; clicking a bar opens Transactions filtered to that category.
 */
function CategorySpend({txs,asOf,currency,onPick}){
  const [days,setDays]=useState(30),[hover,setHover]=useState(null);
  const from=days?iso(new Date(new Date(asOf).getTime()-(days-1)*864e5)):'';
  const groups={};
  for(const t of txs){if(t.kind!=='expense'||t.amount_minor>=0||t.date>asOf||(days&&t.date<from))continue;const c=t.category||'uncategorized';(groups[c]??={category:c,amount:0,count:0});groups[c].amount-=t.amount_minor;groups[c].count+=1;}
  const all=Object.values(groups).sort((a,b)=>b.amount-a.amount),total=all.reduce((s,r)=>s+r.amount,0);
  const named=all.filter(r=>r.category!=='uncategorized');
  const most=named[0],least=named.length>1?named.at(-1):null;
  const shown=all.length>9?[...all.slice(0,8),{category:'other',label:`Other (${all.length-8} categories)`,amount:all.slice(8).reduce((s,r)=>s+r.amount,0),count:all.slice(8).reduce((s,r)=>s+r.count,0)}]:all;
  const max=shown[0]?.amount||1,pct=a=>total?Math.round(a*100/total):0,label=r=>r.label||titleCase(r.category);
  const period=spendPeriods.find(p=>p[0]===days)[1].toLowerCase();
  const pick=r=>onPick({categories:r.category==='other'?all.slice(8).map(x=>x.category):[r.category],label:label(r),from,to:asOf,period});
  return <section className="panel category-panel"><div className="section-title"><div><h2>Where your money goes</h2><p className="muted">Spending by category, {period} to {asOf}. Transfers, refunds and loan repayments are left out. Click a category to see its transactions.</p></div>
    <div className="segmented" role="group" aria-label="Period">{spendPeriods.map(([d,l])=><button key={d} type="button" aria-pressed={days===d} className={days===d?'active':''} onClick={()=>setDays(d)}>{l}</button>)}</div></div>
    {!all.length?<p className="muted empty-line">No spending in this period yet.</p>:<>
    <div className="category-tiles">
      {most&&<div className="cat-tile"><span><TrendingUp size={15}/>Most expensive</span><strong>{titleCase(most.category)}</strong><small>{money(most.amount,currency)} · {pct(most.amount)}% of spending · {most.count} transaction{most.count===1?'':'s'}</small></div>}
      {least&&<div className="cat-tile"><span><ArrowDownLeft size={15}/>Least expensive</span><strong>{titleCase(least.category)}</strong><small>{money(least.amount,currency)} · {pct(least.amount)}% of spending · {least.count} transaction{least.count===1?'':'s'}</small></div>}
      <div className="cat-tile"><span><Wallet size={15}/>Total spent</span><strong>{money(total,currency)}</strong><small>across {all.length} categor{all.length===1?'y':'ies'}{groups.uncategorized?` · ${money(groups.uncategorized.amount,currency)} still uncategorized`:''}</small></div>
    </div>
    <ul className="cat-bars" aria-label="Spending by category">{shown.map(r=><li key={r.category} tabIndex={0} role="button" onClick={()=>pick(r)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();pick(r);}}} onMouseEnter={()=>setHover(r.category)} onMouseLeave={()=>setHover(null)} onFocus={()=>setHover(r.category)} onBlur={()=>setHover(null)} aria-label={`${label(r)}: ${money(r.amount,currency)}, ${pct(r.amount)}% of spending, ${r.count} transactions. Show these transactions`}>
      <span className="cat-name">{label(r)}</span>
      <span className="cat-track"><i className={r.category==='uncategorized'||r.category==='other'?'quiet':''} style={{width:`${Math.max(r.amount/max*100,1)}%`}}/></span>
      <span className="cat-value">{money(r.amount,currency)}</span>
      {hover===r.category&&<span className="cat-tip" role="tooltip"><strong>{label(r)}</strong>{money(r.amount,currency)} · {pct(r.amount)}% of spending<br/>{r.count} transaction{r.count===1?'':'s'}, about {money(Math.round(r.amount/r.count),currency)} each<br/><em>Click to see them</em></span>}
    </li>)}</ul></>}
  </section>;
}

/**
 * The forecast line chart, with the what-if purchase line when one is set.
 *
 * @param props.baseline forecast
 * @param props.scenario simulated timeline or null
 * @param props.currency ISO currency code, e.g. INR
 * @returns SVG chart.
 * Used in: Overview.
 */
function Chart({baseline, scenario, currency}) {
  const rows = baseline.timeline;
  const value = row => row.cash_minor ?? row.cumulative_flow_minor;
  const banded = rows[0]?.cash_low_minor != null;
  const all = [...rows.map(value), ...(scenario?.timeline.map(value) || []), ...(banded ? rows.flatMap(r=>[r.cash_low_minor,r.cash_high_minor]) : [])];
  const low = Math.min(...all), high = Math.max(...all), span = Math.max(high-low,100);
  const xy = (row, i) => `${50+i*690/Math.max(rows.length-1,1)},${185-(value(row)-low)/span*145}`;
  const line = rows.map(xy).join(' ');
  const at = (i, v) => `${50+i*690/Math.max(rows.length-1,1)},${185-(v-low)/span*145}`;
  const band = banded ? [...rows.map((r,i)=>at(i,r.cash_high_minor)), ...rows.map((r,i)=>at(i,r.cash_low_minor)).reverse()].join(' ') : null;
  return <div className="chart"><svg viewBox="0 0 780 240" role="img" aria-label={`Daily projected balance. Solid line: expected balance.${banded?' Shaded area: likely range, 10th to 90th percentile.':''} Dashed line: purchase scenario.`}><defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#3d8066" stopOpacity=".17"/><stop offset="100%" stopColor="#3d8066" stopOpacity="0"/></linearGradient></defs>
    {[0,.5,1].map(n=><g key={n}><line x1="50" x2="740" y1={185-n*145} y2={185-n*145} stroke="#e8ebe5" strokeDasharray="4 5"/><text x="50" y={176-n*145} className="chart-label">{money(low+n*span,currency)}</text></g>)}
    {band ? <polygon points={band} fill="#3d8066" fillOpacity=".14"/> : <polygon points={`50,200 ${line} 740,200`} fill="url(#fill)"/>}<polyline points={line} fill="none" stroke="#245c46" strokeWidth="3" strokeLinejoin="round"/>
    {scenario && <polyline points={scenario.timeline.map(xy).join(' ')} fill="none" stroke="#c47c39" strokeWidth="2.5" strokeDasharray="7 5"/>}
    <circle cx="740" cy={185-(value(rows.at(-1))-low)/span*145} r="4" fill="#245c46"/>
    <text x="50" y="228" className="chart-label">{rows[0].date}</text><text x="740" y="228" textAnchor="end" className="chart-label">{rows.at(-1).date}</text>
  </svg>{banded&&<p className="band-note"><i aria-hidden="true"/>Likely range: 8 in 10 simulated futures end between {money(rows.at(-1).cash_low_minor,currency)} and {money(rows.at(-1).cash_high_minor,currency)}.{baseline.below_reserve_probability!=null?` ${Math.round(baseline.below_reserve_probability*100)}% chance of dipping below your reserve.`:''} Everyday spending forecast by {baseline.model}, a time-series model; your income and bills are exact.</p>}</div>;
}

/**
 * Sign-in and create-account page (customer or admin).
 *
 * @param props.onAuth called with the session after success
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.admin true for the /admin sign-in
 * @returns Page element.
 * Used in: The first screen and /admin.
 */
function Auth({onAuth, api, admin=false}) {
  const [register,setRegister]=useState(()=>new URLSearchParams(window.location.search).has('ref')), [error,setError]=useState(''), [busy,setBusy]=useState(false);
  async function submit(e){e.preventDefault();setError('');setBusy(true);const data=Object.fromEntries(new FormData(e.currentTarget));if(!data.referral_code)delete data.referral_code;try{onAuth(await api(admin?'/ops/v1/auth/login':`/api/v1/auth/${register?'register':'login'}`,{method:'POST',body:JSON.stringify(data)}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  if(admin)return <main className="auth"><section className="auth-story admin-story"><div className="brand light"><img className="brand-mark" src="/finpilot-mark.svg" alt=""/>FinPilot</div><div><span className="eyebrow">OPERATIONS</span><h1>The admin<br/>portal.</h1><p>Activity, logs, Grafana and LangSmith.<br/>What happened, never what customers entered.</p></div><div className="auth-foot"><ShieldCheck size={18}/> Separate sign-in from the customer app.</div></section><section className="auth-form"><span className="badge">ADMIN</span><h2>Operator sign-in</h2><p className="muted">Use an account created with <code>create-operator</code>.</p><form onSubmit={submit}><label>Username<input name="username" required minLength="3" maxLength="40" autoComplete="username" pattern="[a-zA-Z0-9_.\-]+" placeholder="Operator username"/></label><label>Password<input name="password" required minLength="12" maxLength="128" autoComplete="current-password" type="password" placeholder="At least 12 characters"/></label>{error&&<p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy?'Please wait…':'Sign in to admin'}<ArrowRight size={17}/></button></form><a className="text-button" href="/">Go to the customer app</a></section></main>;
  return <main className="auth"><section className="auth-story"><div className="brand light"><img className="brand-mark" src="/finpilot-mark.svg" alt=""/>FinPilot</div><div><span className="eyebrow">A LITTLE CLARITY GOES A LONG WAY</span><h1>Your money.<br/>A clearer<br/><em>way forward.</em></h1><p>Understand your spending. See what’s ahead.<br/>Make room for the things that matter.</p></div><div className="auth-foot"><ShieldCheck size={18}/> Your financial view belongs to you.</div></section><section className="auth-form"><span className="badge">LOCAL DEVELOPMENT · 0.1</span><h2>{register?'Make a fresh start.':'Welcome back.'}</h2><p className="muted">{register?'Create an account to explore your financial trajectory.':'Sign in to see the bigger picture.'}</p><form onSubmit={submit}><label>Username<input name="username" required minLength="3" maxLength="40" autoComplete="username" pattern="[a-zA-Z0-9_.\-]+" placeholder="Your username"/></label><label>Password<input name="password" required minLength="12" maxLength="128" autoComplete={register?'new-password':'current-password'} type="password" placeholder="At least 12 characters"/></label>{register&&<label>Referral code (optional)<input name="referral_code" maxLength="12" pattern="[A-Za-z0-9]{6,12}" defaultValue={new URLSearchParams(window.location.search).get('ref')||''} placeholder="From a friend"/></label>}{error&&<p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy?'Please wait…':register?'Create account':'Sign in'}<ArrowRight size={17}/></button></form><button className="text-button" onClick={()=>{setRegister(!register);setError('');}}>{register?'Already have an account? Sign in':'New here? Create an account'}</button><a className="text-button admin-link" href="/admin">Admin? Sign in here</a><p className="auth-note">An early working build. Start with synthetic data.<br/>AI advice and scheduled reminders are coming later.</p></section></main>;
}

/**
 * The Your context form: currency, dates, cash, reserve and upcoming money in and out.
 *
 * @param props.profile saved profile or null
 * @param props.save saves the edited profile
 * @param props.busy disables Save while saving
 * @returns Form element.
 * Used in: Your context.
 */
function ContextForm({profile,save,busy}){
  const [draft,setDraft]=useState(profile||initialProfile);
  useEffect(()=>setDraft(profile||initialProfile),[profile]);
  const field=(key,value)=>setDraft({...draft,[key]:value});
  return <form className="panel context-form" onSubmit={e=>{e.preventDefault();save(draft);}}><div className="section-title"><div><span className="eyebrow">YOUR STARTING POINT</span><h2>A little context. A better forecast.</h2><p className="muted">One consolidated cash account for this first version. Balances are optional.</p></div><Wallet className="muted"/></div><div className="form-grid"><label>Currency<select value={draft.currency} onChange={e=>field('currency',e.target.value)}>{['INR','AED','USD','EUR','GBP'].map(c=><option key={c}>{c}</option>)}</select></label><label>Timezone<input value={draft.timezone} onChange={e=>field('timezone',e.target.value)} required/></label><label>Balance as of<input type="date" required value={draft.as_of} onChange={e=>field('as_of',e.target.value)}/></label><label>History starts<input type="date" required max={draft.as_of} value={draft.coverage_start} onChange={e=>field('coverage_start',e.target.value)}/></label><label>Available cash ({draft.currency})<input type="number" step=".01" value={draft.opening_balance_minor==null?'':draft.opening_balance_minor/100} onChange={e=>field('opening_balance_minor',amount(e.target.value))} placeholder="Leave blank if unknown"/></label><label>Cash to keep in reserve ({draft.currency})<input type="number" min="0" step=".01" value={draft.reserve_minor==null?'':draft.reserve_minor/100} onChange={e=>field('reserve_minor',amount(e.target.value))} placeholder="Your safety buffer"/></label></div><label className="check-label"><input type="checkbox" checked={draft.coverage_confirmed} onChange={e=>field('coverage_confirmed',e.target.checked)}/>I have included all transactions for this history period.</label><div className="section-title small"><div><h3>Upcoming money in & out</h3><p className="muted">Enter dated paydays and bills. Positive amounts come in; negative amounts go out.</p></div><button type="button" className="secondary" onClick={()=>field('scheduled_flows',[...draft.scheduled_flows,{date:'',label:'',amount_minor:0}])}><Plus size={15}/>Add</button></div>{draft.scheduled_flows.map((flow,i)=><div className="flow-row" key={i}><input aria-label={`Flow ${i+1} date`} type="date" required value={flow.date} onChange={e=>field('scheduled_flows',draft.scheduled_flows.map((f,j)=>j===i?{...f,date:e.target.value}:f))}/><input aria-label={`Flow ${i+1} description`} required maxLength="80" placeholder="Salary, rent…" value={flow.label} onChange={e=>field('scheduled_flows',draft.scheduled_flows.map((f,j)=>j===i?{...f,label:e.target.value}:f))}/><input aria-label={`Flow ${i+1} amount`} type="number" step=".01" required value={flow.amount_minor==null?'':flow.amount_minor/100} onChange={e=>field('scheduled_flows',draft.scheduled_flows.map((f,j)=>j===i?{...f,amount_minor:amount(e.target.value)}:f))}/><button type="button" className="icon-button" aria-label="Remove scheduled flow" onClick={()=>field('scheduled_flows',draft.scheduled_flows.filter((_,j)=>j!==i))}><X size={16}/></button></div>)}<button className="primary" disabled={busy}>Save context<Check size={16}/></button></form>;
}

/**
 * Hook for modal dialogs: focus the first control, trap Tab and close on Escape.
 *
 * @param close function that closes the dialog
 * @returns Nothing.
 * Used in: Import, manual entry, category and investigator dialogs.
 */
function useDialog(close){
  useEffect(()=>{
    const previous=document.activeElement;
    const dialog=document.querySelector('[role="dialog"]');
    const focusable=()=>[...(dialog?.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]')||[])];
    focusable()[0]?.focus();
    function key(e){if(e.key==='Escape'){close();return;}if(e.key==='Tab'){const nodes=focusable(),first=nodes[0],last=nodes.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}}
    document.addEventListener('keydown',key);
    return ()=>{document.removeEventListener('keydown',key);previous?.focus();};
  },[]);
}

/**
 * Import transactions from CSV: preview, then confirm; shows progress while categorizing.
 *
 * @param props.close close the dialog
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.version owner's data version
 * @param props.onDone refresh after import
 * @param props.currency ISO currency code, e.g. INR
 * @returns Modal element.
 * Used in: "Import transactions" on every page.
 */
function ImportModal({close,api,version,onDone,currency}){
  useDialog(close);
  const [preview,setPreview]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[ack,setAck]=useState(false);
  async function file(e){setError('');setBusy(true);try{const f=e.target.files[0];if(!f)return;if(f.size>500000)throw Error('Use a CSV smaller than 500 KB.');setPreview(await api('/api/v1/imports',{method:'POST',body:JSON.stringify({csv_text:await f.text()})}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function confirm(){setBusy(true);setError('');try{await api(`/api/v1/imports/${preview.id}/confirm`,{method:'POST',body:JSON.stringify({expected_version:version,acknowledge_possible_duplicates:ack})});await onDone();close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="import-title"><div className="section-title"><h2 id="import-title">Bring your transactions in</h2><button className="icon-button" aria-label="Close import" onClick={close}><X/></button></div><p className="muted">Preview first, then confirm. We accept up to 1,000 rows per file.</p><label className="upload-zone"><FileUp size={30}/><strong>Choose a CSV file</strong><span>Amounts in minor units: 12500 means {money(12500,currency)}.</span><input type="file" accept=".csv,text/csv" onChange={file} disabled={busy}/></label><p className="small-text">Required headers: <code>date,description,amount_minor,currency,kind</code><br/>Use ISO dates, signed integers, and expense/income/transfer/refund/borrowing/repayment.</p>{busy&&!preview&&<div className="import-busy" role="status"><FinMark animated/><span>Reading your file and categorizing it. Each new merchant is checked once, so a large statement can take up to a minute.</span></div>}{preview&&<><div className="preview-table"><table><thead><tr><th>Date</th><th>Description</th><th>Amount</th></tr></thead><tbody>{preview.rows.slice(0,8).map((r,i)=><tr key={i}><td>{r.date}</td><td>{r.description}</td><td>{money(r.amount_minor,r.currency)}</td></tr>)}</tbody></table></div><p>{preview.rows.length} rows · {preview.confirmed?'Already imported':'Ready for review'}</p><label className="check-label"><input type="checkbox" checked={ack} onChange={e=>setAck(e.target.checked)}/>If overlapping rows are reported, I have reviewed them and want to keep them.</label><button className="primary" disabled={busy||preview.confirmed} onClick={confirm}>{busy?'Importing…':'Confirm import'}<ArrowRight size={16}/></button></>}{error&&<p className="error" role="alert">{error}</p>}</section></div>;
}

/**
 * Add one transaction by hand.
 *
 * @param props.close close
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.currency ISO currency code, e.g. INR
 * @param props.onDone refresh after adding
 * @returns Modal element.
 * Used in: "Add transaction" on Transactions.
 */
function ManualModal({close,api,currency,onDone}){
  useDialog(close);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  const key=useRef(crypto.randomUUID());
  async function submit(e){e.preventDefault();setBusy(true);setError('');const d=Object.fromEntries(new FormData(e.currentTarget));try{await api('/api/v1/transactions',{method:'POST',body:JSON.stringify({idempotency_key:key.current,transaction:{date:d.date,description:d.description,amount_minor:amount(d.amount),currency,kind:d.kind,category:d.category.trim()||null,recurring:d.recurring==='on'}})});await onDone();close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <div className="modal-backdrop"><form className="modal" role="dialog" aria-modal="true" aria-labelledby="manual-title" onSubmit={submit}><div className="section-title"><h2 id="manual-title">Add a transaction</h2><button type="button" className="icon-button" onClick={close} aria-label="Close transaction form"><X/></button></div><label>Date<input required type="date" name="date" defaultValue={today}/></label><label>Description<input required name="description" maxLength="240" placeholder="What was it for?"/></label><div className="form-grid"><label>Signed amount ({currency})<input required type="number" step=".01" name="amount" placeholder="-250.00"/></label><label>Type<select name="kind">{['expense','income','transfer','refund','borrowing','repayment'].map(k=><option key={k}>{k}</option>)}</select></label></div><label>Category (optional)<input name="category" maxLength="64" placeholder="Leave blank for AI to create a category"/></label><label className="check-label"><input type="checkbox" name="recurring"/>Recurring bill · I will enter its future dates in Context.</label><p className="muted small-text">Expenses and repayments use negative amounts. Transfers are excluded from income and spending.</p>{error&&<p className="error" role="alert">{error}</p>}<button className="primary" disabled={busy}>Save transaction<Check size={16}/></button></form></div>;
}

/**
 * Fix a transaction's category: ask the AI with a note or set it yourself; shows AI requests left today.
 *
 * @param props.tx the transaction
 * @param props.close close
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.version data version
 * @param props.onDone called with the result (shows "also updated N similar")
 * @param props.currency ISO currency code, e.g. INR
 * @param props.limit AI requests left today
 * @param props.aiReady whether AI is available
 * @returns Modal element.
 * Used in: Clicking a category on Transactions.
 */
function CategoryModal({tx,close,api,version,onDone,currency,limit,aiReady}){
  useDialog(close);
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function send(e,body){e.preventDefault();setBusy(true);setError('');try{await onDone(await api(`/api/v1/transactions/${tx.id}/category`,{method:'POST',body:JSON.stringify({expected_version:version,...body})}));close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  const value=e=>new FormData(e.currentTarget).values().next().value.trim();
  const left=aiReady?limit:0;
  return <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-labelledby="category-title"><div className="section-title"><h2 id="category-title">Fix this category</h2><button className="icon-button" aria-label="Close category editor" onClick={close}><X/></button></div><div className="tx-summary"><div><strong>{tx.description}</strong><span className="muted">{tx.date} · {money(tx.amount_minor,currency)} · {tx.kind}</span></div><span className={`category ${tx.category==='uncategorized'?'unresolved':''}`}>{tx.category}</span></div><p className="muted reason">{categoryReason(tx)}</p>{tx.category_source==='model'&&<Feedback api={api} agent="categorizer" surface="category" source="ai" itemRef={`category-${tx.id}`} label="Was the AI's category right?"/>}<p className="learn-note"><Sparkles size={14}/>Your answer applies to every transaction from this merchant, now and in future imports.</p><form onSubmit={e=>send(e,{note:value(e)})}><label>Tell AI what it was<input name="note" required maxLength="240" placeholder="e.g. Books for personal reading, home deep cleaning" defaultValue={tx.category_note||''} disabled={!left}/></label><div className="attempts"><button className="primary" disabled={busy||!left}><Sparkles size={16}/>{busy?'Asking AI…':'Ask AI to categorize'}</button><span className="muted">{!aiReady?'AI suggestions are unavailable right now. Set the category yourself.':left?`${left} AI request${left===1?'':'s'} left today`:"You've used today's AI requests. Set the category yourself, or ask again tomorrow."}</span></div></form><div className="divider"><span>or</span></div><form onSubmit={e=>send(e,{category:value(e)})}><label>Set the category yourself<input name="category" required maxLength="64" placeholder="e.g. Books"/></label><button className="secondary" disabled={busy}>Save my category<Check size={16}/></button></form>{error&&<p className="error" role="alert">{error}</p>}</section></div>;
}

/**
 * "A few questions from Fina": suggested income, bills, cash, reserve and history confirmation.
 *
 * @param props.close close
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.profile profile
 * @param props.version data version
 * @param props.onSaved called with how many answers were saved
 * @param props.currency ISO currency code, e.g. INR
 * @returns Modal element.
 * Used in: "Ask Fina what's missing" and Fina's chat.
 */
function InvestigatorModal({close,api,profile,version,onSaved,currency}){
  useDialog(close);
  const [questions,setQuestions]=useState(null),[source,setSource]=useState(''),[answers,setAnswers]=useState({}),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{api('/api/v1/investigator/questions',{method:'POST'}).then(r=>{setQuestions(r.questions);setSource(r.source);setAnswers(Object.fromEntries(r.questions.filter(q=>q.proposal).map(q=>[q.ref,{use:null,label:q.proposal.label,amount:String(q.proposal.amount_minor/100),date:q.proposal.date}])));}).catch(e=>setError(e.message));},[]);
  const set=(ref,patch)=>setAnswers(a=>({...a,[ref]:{...a[ref],...patch}}));
  const flowOf=a=>({date:a.date,label:(a.label||'').trim()||'Upcoming payment',amount_minor:amount(a.amount)});
  const chosen=(questions||[]).filter(q=>{const a=answers[q.ref];if(!a)return false;if(q.kind==='upcoming_flow')return a.use===true;if(q.kind==='next_income')return a.date&&a.amount;if(q.kind==='coverage')return a.use===true;return a.amount!==undefined&&a.amount!=='';});
  async function save(){setBusy(true);setError('');try{let next={...profile,scheduled_flows:[...profile.scheduled_flows]};for(const q of chosen){const a=answers[q.ref];
    if(q.kind==='upcoming_flow'||q.kind==='next_income')next.scheduled_flows.push(flowOf(q.kind==='next_income'?{...a,label:a.label||'Salary'}:a));
    if(q.kind==='available_cash')next.opening_balance_minor=amount(a.amount);
    if(q.kind==='reserve')next.reserve_minor=amount(a.amount);
    if(q.kind==='coverage')next.coverage_confirmed=true;}
    await api('/api/v1/profile',{method:'PUT',body:JSON.stringify({profile:next,expected_version:version})});await onSaved(chosen.length);close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <div className="modal-backdrop"><section className="modal investigator" role="dialog" aria-modal="true" aria-labelledby="inv-title"><div className="section-title"><div className="inv-head"><span className="fina-avatar" aria-hidden="true">F</span><div><h2 id="inv-title">A few questions from Fina</h2><p className="muted">Your answers make the forecast sharper. Nothing is saved until you confirm it.</p></div></div><button className="icon-button" aria-label="Close questions" onClick={close}><X/></button></div>
    {!questions&&!error&&<div className="chat-msg assistant typing">Fina is looking at what's missing…</div>}
    {questions&&!questions.length&&<p className="muted">Nothing is missing right now. Your forecast has what it needs.</p>}
    {(questions||[]).map(q=>{const a=answers[q.ref]||{};return <div key={q.ref} className={`inv-q ${q.kind==='upcoming_flow'&&a.use===false?'skipped':''}`}><p>{q.question}</p>
      {q.kind==='upcoming_flow'&&<><div className="inv-suggest"><input aria-label="Label" value={a.label} maxLength="80" onChange={e=>set(q.ref,{label:e.target.value})}/><input aria-label={`Amount (${currency})`} type="number" step=".01" value={a.amount} onChange={e=>set(q.ref,{amount:e.target.value})}/><input aria-label="Date" type="date" min={profile.as_of} value={a.date} onChange={e=>set(q.ref,{date:e.target.value})}/></div><div className="inv-choice"><button type="button" className={a.use===true?'primary':'secondary'} onClick={()=>set(q.ref,{use:true})}><Check size={14}/>Yes, add it</button><button type="button" className="secondary" onClick={()=>set(q.ref,{use:false})}>Skip</button><small className="muted">{(Number(a.amount)||0)>=0?'Money coming in':'Money going out'} · edit anything first</small></div></>}
      {(q.kind==='available_cash'||q.kind==='reserve')&&<input aria-label={q.kind==='reserve'?'Reserve amount':'Available cash'} type="number" step=".01" min={q.kind==='reserve'?'0':undefined} placeholder={`Amount in ${currency} (optional)`} value={a.amount??''} onChange={e=>set(q.ref,{amount:e.target.value})}/>}
      {q.kind==='next_income'&&<div className="inv-suggest"><input aria-label="Income label" placeholder="Salary" value={a.label??''} maxLength="80" onChange={e=>set(q.ref,{label:e.target.value})}/><input aria-label={`Income amount (${currency})`} type="number" step=".01" min=".01" placeholder="Amount" value={a.amount??''} onChange={e=>set(q.ref,{amount:e.target.value})}/><input aria-label="Next payday" type="date" min={profile.as_of} value={a.date??''} onChange={e=>set(q.ref,{date:e.target.value})}/></div>}
      {q.kind==='coverage'&&<div className="inv-choice"><button type="button" className={a.use===true?'primary':'secondary'} onClick={()=>set(q.ref,{use:true})}><Check size={14}/>Yes, it's complete</button><button type="button" className="secondary" onClick={()=>set(q.ref,{use:false})}>Not yet</button></div>}
    </div>;})}
    {error&&<p className="error" role="alert">{error}</p>}
    {questions&&questions.length>0&&<div className="inv-foot"><small className="muted">{source==='ai'?'Fina · spotted from your history, checked against your transactions':'Fina · worked out from your history'}</small><Feedback api={api} agent="investigator" surface="investigator" source={source} label="Good questions?"/><button className="primary" disabled={busy||!chosen.length} onClick={save}>{busy?'Saving…':`Save ${chosen.length||''} answer${chosen.length===1?'':'s'}`}<Check size={16}/></button></div>}
  </section></div>;
}

const goalStatus = {on_track:['On track','good'], stretch:['A stretch','warn'], off_track:['Off track','bad'], needs_forecast:['Needs a forecast','muted'], reached:['Reached','good'], past_due:['Date passed','warn']};

// Any API response carrying `unlocked` (goal completed, achievement earned) queues a celebration here.
const confettiColors=['#e6b321','#2f6b51','#9cbf7a','#c47a3c','#9fd4e6','#c2413b','#fff4b8'];
/**
 * Queue of celebrations from any API response carrying `unlocked`; shows one pop-up at a time.
 * @returns The current pop-up or nothing.
 * Used in: Mounted once in the app; triggered by goals, rewards, debts, invest and redeem.
 */
function Celebration(){
  const [queue,setQueue]=useState([]);
  useEffect(()=>{const on=e=>setQueue(q=>[...q,...e.detail.filter(u=>!q.some(x=>x.id===u.id))]);window.addEventListener('fina-unlocked',on);return()=>window.removeEventListener('fina-unlocked',on);},[]);
  return queue.length?<CelebrationCard key={queue[0].id} item={queue[0]} more={queue.length>1} next={()=>setQueue(q=>q.slice(1))}/>:null;
}
/**
 * The goal or achievement pop-up: spinning 3D token, rays, sparks, confetti and points.
 *
 * @param props.item {id, kind, title, about, bonus, tier}
 * @param props.more whether more pop-ups are queued
 * @param props.next dismiss
 * @returns Overlay element.
 * Used in: "Goal complete" and "Achievement unlocked".
 */
function CelebrationCard({item,more,next}){
  const pieces=useRef(Array.from({length:70},(_,i)=>({left:Math.random()*100,delay:Math.random()*0.6,duration:2.2+Math.random()*1.8,spin:Math.random()*720-360,drift:Math.random()*120-60,color:confettiColors[i%confettiColors.length],round:i%3===0}))).current;
  useEffect(()=>{const key=e=>{if(e.key==='Escape'||e.key==='Enter'){e.preventDefault();next();}};document.addEventListener('keydown',key);return()=>document.removeEventListener('keydown',key);},[]);
  const goal=item.kind==='goal';
  return <div className="celebrate" role="alertdialog" aria-modal="true" aria-labelledby="celebrate-title" onClick={next}>
    <div className="confetti" aria-hidden="true">{pieces.map((p,i)=><i key={i} className={p.round?'round':''} style={{left:`${p.left}%`,background:p.color,animationDelay:`${p.delay}s`,animationDuration:`${p.duration}s`,'--spin':`${p.spin}deg`,'--drift':`${p.drift}px`}}/>)}</div>
    <div className={`celebrate-card ${item.tier||'gold'}`} onClick={e=>e.stopPropagation()}>
      <div className="celebrate-stage" aria-hidden="true"><div className="rays"/><div className="glow"/><div className={`token big ${item.tier||'gold'}`}><div className="token-face">{goal?<Target size={50}/>:<Award size={54}/>}</div></div>{[0,1,2,3,4,5].map(i=><span key={i} className="spark" style={{'--a':`${i*60}deg`}}/>)}</div>
      <span className="eyebrow">{goal?'GOAL COMPLETE':'ACHIEVEMENT UNLOCKED'}</span>
      <h2 id="celebrate-title">{item.title}</h2>
      <p>{item.about}</p>
      {item.bonus>0&&<strong className="celebrate-points"><Gift size={16}/>+{item.bonus.toLocaleString('en-IN')} FinaPoints</strong>}
      <button className="primary" autoFocus onClick={next}>{more?'Next':'Brilliant!'}<ArrowRight size={15}/></button>
    </div>
  </div>;
}

/**
 * Routes back on track for a goal that fell behind, each with its reasoning.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.goal the goal
 * @returns Panel or nothing when on track.
 * Used in: Goal cards.
 */
function RecoveryRoutes({api,goal}){
  const [routes,setRoutes]=useState(null);
  useEffect(()=>{api(`/api/v1/goals/${goal.id}/recovery`).then(r=>setRoutes(r.routes)).catch(()=>setRoutes([]));},[goal.id,goal.saved_minor,goal.target_minor,goal.target_date]);
  if(!routes?.length)return null;
  const line=r=>r.kind==='catch_up'?`Set aside ${money(r.facts.needed_per_month)} a month: ${money(r.facts.gap_per_month)} more than your current pace.${r.facts.covered_by_cuts?` ${r.facts.cuts.join(' and ')} would cover ${money(r.facts.covered_by_cuts)} of that.`:''}`
    :r.kind==='fix_shortfall'?`Your balance is shrinking by about ${money(r.facts.shrinking_per_month)} a month.${r.facts.covered_by_cuts?` ${r.facts.cuts.join(' and ')} would save ${money(r.facts.covered_by_cuts)}, leaving ${money(r.facts.still_short)} a month still to close.`:''} Once that's closed, a date becomes possible.`
    :r.kind==='new_date'?(r.facts.reach_date?`At ${money(r.facts.monthly)} a month${r.facts.with_cuts?' (with those cuts)':''}, you'd reach it by ${r.facts.reach_date}.`:`At ${money(r.facts.monthly)} a month it would take more than 50 years.`)
    :`Aim for ${money(r.facts.milestone)} by ${r.facts.target_date} first, then carry on to ${money(r.facts.target)}.`;
  return <div className="recovery"><h4><LifeBuoy size={14}/>You've fallen behind. Here's how to get back on track</h4>{routes.map(r=><div key={r.kind} className="advice-item"><strong>{r.title}</strong><p>{line(r)}</p><p className="advice-why">Why: {r.why}</p></div>)}<Feedback api={api} agent="advisor" surface="goal_ideas" source="calculation" label="Useful routes?"/></div>;
}

/**
 * Emergency re-plan: preview how an amount taken out changes every goal, then apply.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.initial starting amount in minor units
 * @param props.onDone refresh goals
 * @param props.close close
 * @returns Panel element.
 * Used in: "Had an emergency?" on Goals and Fina's re-plan button.
 */
function Reroute({api,initial,onDone,close}){
  const [amountText,setAmountText]=useState(initial?String(initial/100):''),[plan,setPlan]=useState(null),[rebuild,setRebuild]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState('');
  async function preview(e){e?.preventDefault();setBusy(true);setError('');try{setPlan(await api('/api/v1/goals/reroute',{method:'POST',body:JSON.stringify({amount_minor:amount(amountText)})}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  useEffect(()=>{if(initial)preview();},[]);
  async function apply(){setBusy(true);try{await api('/api/v1/goals/reroute/apply',{method:'POST',body:JSON.stringify({amount_minor:amount(amountText),create_rebuild_goal:rebuild})});await onDone();close();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <section className="panel reroute"><div className="section-title"><div><h2><LifeBuoy size={18}/> Re-plan after an emergency</h2><p className="muted">Tell me how much you had to use. I'll take it from your goals fairly and re-estimate every date. Nothing changes until you apply it.</p></div><button className="icon-button" aria-label="Close re-plan" onClick={close}><X size={16}/></button></div>
    <form className="flow-row" onSubmit={preview}><input aria-label="Amount used" type="number" min="1" step=".01" required placeholder="Amount used (₹)" value={amountText} onChange={e=>setAmountText(e.target.value)}/><button className="secondary" disabled={busy}>Preview</button></form>
    {error&&<p className="error">{error}</p>}
    {plan&&<><p className="muted small-text">{plan.why}{plan.from_cash>0?` Your goals held ${money(plan.from_goals)}; the other ${money(plan.from_cash)} comes from your cash.`:''}</p>
      <div className="table-wrap"><table><thead><tr><th>Goal</th><th className="right">Saved</th><th className="right">After</th><th>Expected before</th><th>Expected now</th><th></th></tr></thead><tbody>{plan.goals.map(g=><tr key={g.id}><td><strong>{g.name}</strong><br/><small className="muted">target {money(g.target_minor)} by {g.target_date}</small></td><td className="right">{money(g.saved_before)}</td><td className="right">{money(g.saved_after)}</td><td>{g.reach_before||'not at current pace'}</td><td><strong>{g.reach_after||'not at current pace'}</strong></td><td>{g.on_time_after?<span className="goal-pill good">on time</span>:<span className="goal-pill warn">later</span>}</td></tr>)}</tbody></table></div>
      <label className="check-label"><input type="checkbox" checked={rebuild} onChange={e=>setRebuild(e.target.checked)}/>Also add a goal to rebuild {money(plan.rebuild_goal.target_minor)} by {plan.rebuild_goal.target_date}</label>
      <button className="primary" disabled={busy} onClick={apply}>{busy?'Applying…':'Apply this plan'}<Check size={16}/></button></>}
  </section>;
}

/**
 * One goal: progress, pace, plan date, savings proof, coaching, ideas and recovery routes.
 *
 * @param props.goal goal with progress, plan and proof
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.currency ISO currency code, e.g. INR
 * @param props.onChange reload goals
 * @returns Card element.
 * Used in: Goals.
 */
function GoalCard({goal,api,currency,onChange}){
  const p=goal.progress,[saved,setSaved]=useState(String(goal.saved_minor/100)),[note,setNote]=useState(null),[ideas,setIdeas]=useState(null),[busy,setBusy]=useState(''),[error,setError]=useState('');
  const [done,setDone]=useState(goal.status==='achieved');
  useEffect(()=>setSaved(String(goal.saved_minor/100)),[goal.saved_minor]);
  useEffect(()=>setDone(goal.status==='achieved'),[goal.status]);
  async function run(kind,fn){setBusy(kind);setError('');try{await fn();}catch(e){setError(e.message);}finally{setBusy('');}}
  const [points,setPoints]=useState(0);
  const patch=body=>run('save',async()=>{const r=await api(`/api/v1/goals/${goal.id}`,{method:'PATCH',body:JSON.stringify(body)});setPoints(r.points_change||0);await onChange();});
  const [label,tone]=goalStatus[p.status]||[p.status,'muted'];
  return <div className={`goal-card ${done?'achieved':''}`}>
    <div className="goal-top"><label className="goal-check"><input type="checkbox" checked={done} onChange={e=>{setDone(e.target.checked);patch({status:e.target.checked?'achieved':'active'});}} aria-label={`Mark ${goal.name} achieved`}/><div><h3>{goal.name}</h3><p className="muted">{money(goal.target_minor,currency)} by {goal.target_date}</p></div></label><span className={`goal-pill ${tone}`}>{label}</span><button className="icon-button" aria-label={`Remove ${goal.name}`} onClick={()=>run('delete',async()=>{await api(`/api/v1/goals/${goal.id}`,{method:'DELETE'});await onChange();})}><Trash2 size={16}/></button></div>
    <div className="goal-bar" role="progressbar" aria-valuenow={p.percent_saved} aria-valuemin="0" aria-valuemax="100"><i style={{width:`${p.percent_saved}%`}}/></div>
    <div className="goal-facts">{goal.plan&&goal.plan.reach_date&&p.status!=='reached'&&<span className={goal.plan.on_time?'':'late'}>With all your goals: <strong>{goal.plan.reach_date}</strong></span>}<span><strong>{p.percent_saved}%</strong> set aside</span><span><strong>{money(p.remaining_minor,currency)}</strong> to go</span>{p.status!=='reached'&&p.days_left>0&&<span><strong>{money(p.needed_per_month_minor,currency)}</strong>/month needed</span>}{p.pace_per_month_minor!=null&&<span><strong>{money(p.pace_per_month_minor,currency)}</strong>/month pace</span>}</div>
    <div className="goal-actions"><form onSubmit={e=>{e.preventDefault();patch({saved_minor:Math.max(0,amount(saved)||0)});}}><label className="inline-label">Set aside so far ({currency})<input type="number" min="0" step=".01" value={saved} onChange={e=>setSaved(e.target.value)}/></label><button className="secondary" disabled={busy==='save'}>Update</button></form>
      <div className="goal-buttons">{p.status!=='reached'&&p.status!=='past_due'&&<button className="secondary" disabled={busy==='ideas'} onClick={()=>run('ideas',async()=>setIdeas(await api(`/api/v1/goals/${goal.id}/advice`,{method:'POST'})))}><Target size={14}/>{busy==='ideas'?'Finding ways…':'Ways to get there sooner'}</button>}<button className="text-button" disabled={busy==='coach'} onClick={()=>run('coach',async()=>setNote(await api(`/api/v1/goals/${goal.id}/coach`,{method:'POST'})))}><Sparkles size={14}/>{busy==='coach'?'Fina is thinking…':'Ask Fina'}</button></div></div>
    {(p.status==='stretch'||p.status==='off_track')&&<RecoveryRoutes api={api} goal={goal}/>}
    {points>0&&<p className="points-note up"><Gift size={14}/>+{points.toLocaleString('en-IN')} FinaPoints for reaching this goal!</p>}
    {goal.saved_minor>=goal.target_minor&&!done&&<p className="points-note"><Gift size={14}/>Fully saved! Tick it, and once your transfers into savings show it, you earn FinaPoints.</p>}
    {done&&goal.proof&&(goal.proof.verified?<p className="points-note up proof"><ShieldCheck size={14}/>Proven by your savings transfers. FinaPoints earned.</p>
      :goal.saved_minor>=goal.target_minor&&<p className="points-note proof waiting"><ShieldCheck size={14}/>FinaPoints are waiting for proof: since you set this goal on {goal.proof.since}, your transactions show {money(goal.proof.found_minor,currency)} of {money(goal.proof.needed_minor,currency)} moved into savings. Import or add those transfers to your savings account, FD, RD or SIP and the points follow automatically.</p>)}
    {ideas&&(ideas.paused?<p className="muted small-text goal-ideas">Suggestions are paused right now.</p>:ideas.items.length?<div className="goal-ideas"><h4>Ways to get there sooner</h4><AdviceList items={ideas.items} source={ideas.source} api={api} surface="goal_ideas"/></div>:<p className="muted small-text goal-ideas">I couldn't find a subscription or non-essential spending to trim for this goal. Your spending already looks lean.</p>)}
    {note&&<div className="chat-msg assistant goal-note">{note.note}<small>{note.source==='ai'?'Fina · numbers from your goal':'Fina · worked out from your goal'}</small><Feedback api={api} agent="goals" surface="goal_note" source={note.source}/></div>}
    {error&&<p className="error" role="alert">{error}</p>}
  </div>;
}

/**
 * The Goals page: goals list, emergency re-plan and the Add a goal form.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.currency ISO currency code, e.g. INR
 * @param props.setPage navigate
 * @param props.reloadKey changes to force a reload
 * @param props.reroute re-plan amount or null
 * @param props.setReroute open or close the re-plan
 * @returns Page element.
 * Used in: Goals.
 */
function Goals({api,currency,setPage,reloadKey,reroute,setReroute}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function load(){try{setData(await api('/api/v1/goals'));}catch(e){setError(e.message);}}
  useEffect(()=>{load();},[reloadKey]);
  async function add(e){e.preventDefault();const form=e.currentTarget,d=Object.fromEntries(new FormData(form));setBusy(true);setError('');try{await api('/api/v1/goals',{method:'POST',body:JSON.stringify({name:d.name.trim(),target_minor:amount(d.target),target_date:d.date,saved_minor:amount(d.saved)||0})});form.reset();await load();}catch(e){setError(e.message);}finally{setBusy(false);}}
  const active=(data?.goals||[]).filter(g=>g.status!=='achieved'),done=(data?.goals||[]).filter(g=>g.status==='achieved');
  return <>{error&&<div className="error banner" role="alert">{error}</div>}
    {reroute!==null&&<Reroute api={api} initial={reroute} onDone={load} close={()=>setReroute(null)}/>}
    {data&&!data.has_forecast&&data.goals.length>0&&<div className="attention"><div><strong>Create a forecast to see your pace.</strong><p>FinPilot compares each goal with how your balance is moving.</p></div><button className="secondary" onClick={()=>setPage('overview')}>Go to Overview</button></div>}
    <section className="panel"><div className="section-title"><div><h2>Your goals</h2><p className="muted">Tick a goal when it's done. Progress, monthly need and pace are calculated from your own numbers.</p></div><div className="button-row tight">{active.length>0&&reroute===null&&<button className="secondary" onClick={()=>setReroute(0)}><LifeBuoy size={14}/>Had an emergency?</button>}<span className="badge">{active.length} ACTIVE</span></div></div>
      {!data?<div className="empty"><FinMark animated/>Loading your goals…</div>:!data.goals.length?<div className="empty"><Target size={34}/><h3>What are you saving for?</h3><p>Add a goal below, or tell Fina: “save 50k for a trip by March”.</p></div>:<div className="goal-list">{[...active,...done].map(g=><GoalCard key={g.id} goal={g} api={api} currency={currency} onChange={load}/>)}</div>}
    </section>
    <form className="panel goal-form" onSubmit={add}><h3>Add a goal</h3><div className="form-grid four"><label>What for<input name="name" required maxLength="80" placeholder="Trip to Goa"/></label><label>Target ({currency})<input name="target" type="number" min=".01" step=".01" required placeholder="50000"/></label><label>By<input name="date" type="date" required/></label><label>Already set aside<input name="saved" type="number" min="0" step=".01" placeholder="0"/></label></div><button className="primary" disabled={busy}><Plus size={16}/>{busy?'Adding…':'Add goal'}</button></form>
  </>;
}

/**
 * One debt row; only the amount owed and the yearly rate are editable, the rest comes from history.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.d debt (tracked) or detected obligation
 * @param props.tracked true for a tracked debt
 * @param props.reload refresh
 * @returns Table row.
 * Used in: Outlook > Debts and fixed payments.
 */
function DebtRow({api,d,tracked,reload}){
  const rent=d.kind==='rent',owedNow=tracked?String(d.balance_minor/100):'',rateNow=tracked?String(d.rate_percent):'';
  const [owed,setOwed]=useState(owedNow),[rate,setRate]=useState(rateNow),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{setOwed(owedNow);setRate(rateNow);},[owedNow,rateNow]);
  const changed=tracked?owed!==owedNow||rate!==rateNow:owed!==''&&rate!=='';
  async function save(){if(!changed||busy)return;setBusy(true);setError('');try{
    const values={balance_minor:Math.max(0,amount(owed)||0),rate_percent:Math.min(60,Math.max(0,Number(rate)||0))};
    if(tracked)await api(`/api/v1/debts/${d.id}`,{method:'PATCH',body:JSON.stringify(values)});
    else await api('/api/v1/debts',{method:'POST',body:JSON.stringify({name:d.name.slice(0,80),kind:d.kind,monthly_minor:d.monthly_minor,...values})});
    await reload();}catch(e){setError(e.message);}finally{setBusy(false);}}
  const enter=e=>{if(e.key==='Enter'){e.preventDefault();save();}};
  return <tr className={tracked?'':'detected'}>
    <td><strong>{d.name}</strong> <small className="muted">{tracked?'':'found in your history · '}{d.kind.replace('_',' ')}</small>{d.high_interest&&<span className="goal-pill bad small">costly</span>}{error&&<small className="error-text block">{error}</small>}</td>
    <td className="right">{rent?<span className="muted">—</span>:<input className="cell-input" type="number" min="0" step=".01" value={owed} placeholder="Still owed" aria-label={`Amount still owed on ${d.name}`} onChange={e=>setOwed(e.target.value)} onKeyDown={enter}/>}</td>
    <td className="right">{rent?<span className="muted">—</span>:<span className="cell-rate"><input className="cell-input small" type="number" min="0" max="60" step=".1" value={rate} placeholder="Rate" aria-label={`Interest rate a year on ${d.name}`} onChange={e=>setRate(e.target.value)} onKeyDown={enter}/>%</span>}</td>
    <td className="right">{money(d.monthly_minor)}</td>
    <td className={tracked?'':'muted'}>{rent?'ongoing':tracked?d.payoff_date||'never at this payment':'enter owed and rate'}</td>
    <td className="right nowrap">{!rent&&changed&&<button className="secondary small-button" disabled={busy} onClick={save}>{busy?'Saving…':'Save'}</button>}</td></tr>;
}

/**
 * Debts and fixed payments: pressure meter, rows, advice and suggested pay-off goals.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.debts debt agent view
 * @param props.reload refresh
 * @param props.setPage navigate
 * @returns Panel element.
 * Used in: Outlook.
 */
function Debts({api,debts,reload,setPage}){
  const [made,setMade]=useState({});
  async function makeGoal(g){await api('/api/v1/goals',{method:'POST',body:JSON.stringify({name:g.name.slice(0,80),target_minor:g.target_minor,target_date:g.target_date})});setMade(m=>({...m,[g.name]:true}));}
  const tone=debts.pressure==='high'?'bad':debts.pressure==='elevated'?'warn':'good';
  return <section className="panel"><div className="section-title"><div><h2><Landmark size={18}/> Debts and fixed payments</h2><p className="muted">The debt agent tracks loans, EMIs, cards and rent: what they take from your income and how to clear them faster.</p></div>{debts.pressure&&<span className={`goal-pill ${tone}`}>{debts.pressure==='high'?'High pressure':debts.pressure==='elevated'?'Elevated':'Healthy'}</span>}</div>
    {debts.ratio_percent!=null&&<div className="debt-meter"><div className="sat-bar wide"><i style={{width:`${Math.min(debts.ratio_percent,100)}%`}} className={tone==='bad'?'low':tone==='warn'?'mid':''}/></div><p><strong>{debts.ratio_percent}%</strong> of your monthly income ({money(debts.income_minor)}) goes to {money(debts.obligations_minor)} of fixed payments. Under 35% is comfortable; over 50% leaves little room.</p></div>}
    {(debts.debts.length>0||debts.detected.length>0)&&<div className="table-wrap"><table><thead><tr><th>Debt</th><th className="right">Owed</th><th className="right">Rate</th><th className="right">Monthly</th><th>Cleared by</th><th></th></tr></thead><tbody>
      {debts.debts.map(d=><DebtRow key={d.id} api={api} d={d} tracked reload={reload}/>)}
      {debts.detected.map(d=><DebtRow key={d.name} api={api} d={d} tracked={false} reload={reload}/>)}
    </tbody></table></div>}
    {!debts.debts.length&&!debts.detected.length&&<p className="muted small-text">No loans, EMIs or rent found in your history yet. Import your transactions and the debt agent will pick them up.</p>}
    {debts.advice?.length>0&&<div className="warning-advice"><h4>Here's what would help</h4><AdviceList items={debts.advice} source="calculation" api={api} surface="outlook"/></div>}
    {debts.suggested_goals?.length>0&&<div className="suggested"><h4>Make it a goal</h4>{debts.suggested_goals.map(g=><div key={g.name} className="suggested-row"><span>{g.name}: {money(g.target_minor)} by {g.target_date}</span><button className="secondary" disabled={made[g.name]} onClick={()=>makeGoal(g)}>{made[g.name]?'Added to Goals':'Add as goal'}</button></div>)}</div>}
  </section>;
}

/**
 * The Outlook page: rising spending, what it means for goals and the reserve, advice and debts.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.setPage navigate
 * @returns Page element.
 * Used in: Outlook.
 */
function Outlook({api,setPage}){
  const [data,setData]=useState(null),[story,setStory]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const reload=()=>api('/api/v1/outlook').then(setData).catch(e=>setError(e.message));
  useEffect(()=>{reload();},[]);
  async function explain(){setBusy(true);setError('');try{setStory(await api('/api/v1/outlook/explain',{method:'POST'}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  if(error)return <div className="error banner" role="alert">{error}</div>;
  if(!data)return <div className="empty"><FinMark animated/>Looking at your patterns…</div>;
  if(!data.ready)return <><section className="panel"><div className="empty"><TrendingUp size={34}/><h3>I need a little more history.</h3><p>Patterns compare your last 30 days with the 30 before, so Outlook needs at least 60 days of transactions. Move "History starts" earlier in Your context if you have older ones.</p><button className="secondary" onClick={()=>setPage('context')}>Open Your context</button></div></section>{data.debts&&<Debts api={api} debts={data.debts} reload={reload} setPage={setPage}/>}</>;
  const stories=Object.fromEntries((story?.items||[]).map(i=>[i.ref,i]));
  return <>
    <section className="panel"><div className="section-title"><div><h2>Patterns to watch</h2><p className="muted">The behavioural pattern agent compares {data.window?`${data.window.recent[0]} to ${data.window.recent[1]} with ${data.window.prior[0]} to ${data.window.prior[1]}`:'your last 30 days with the 30 before'}, up to your latest transaction. Essentials like rent and groceries are left out.</p></div><span className="badge">{data.patterns.length} RISING</span></div>
      {data.patterns.length>0&&<div className="pattern-list">{data.pattern_texts.map(t=><div key={t} className="pattern-row"><TrendingUp size={18}/><p>{t}</p></div>)}</div>}
      {data.new_texts?.length>0&&<div className="pattern-list new-list"><h4>New this month</h4>{data.new_texts.map(t=><div key={t} className="pattern-row new"><Sparkles size={16}/><p>{t}</p></div>)}</div>}
      {!data.patterns.length&&<><div className="empty small"><Check size={28}/><h3>Nothing is creeping up.</h3><p>None of your non-essential categories rose by 20% and ₹500 or more compared with the month before.</p></div>
        {data.checked?.length>0&&<details className="checked"><summary>What I checked</summary><table><thead><tr><th>Category</th><th className="right">Before</th><th className="right">Latest 30 days</th><th className="right">Change</th></tr></thead><tbody>{data.checked.map(c=><tr key={c.category}><td>{c.category}</td><td className="right">{money(c.prior_month)}</td><td className="right">{money(c.recent_month)}</td><td className="right">{c.change_percent==null?'new':`${c.change_percent>0?'+':''}${c.change_percent}%`}</td></tr>)}</tbody></table></details>}</>}
    </section>
    {data.warnings.length>0&&<section className="panel"><div className="section-title"><div><h2>If this continues…</h2><p className="muted">A projection, not a prediction: what happens if these patterns keep rising at the same rate.</p></div>{!data.has_forecast&&<button className="secondary" onClick={()=>setPage('overview')}>Create a forecast</button>}<button className="secondary" disabled={busy} onClick={explain}><Sparkles size={14}/>{busy?'Fina is reading your patterns…':'Explain with Fina'}</button></div>
      {story?.paused&&<p className="muted small-text">Fina's explanations are paused right now.</p>}
      <div className="warning-list">{data.warnings.map(w=><article key={w.ref} className={`warning-card ${w.kind}`}><header><TriangleAlert size={18}/><h3>{stories[w.ref]?.title||w.title}</h3></header><p className="warning-text">{stories[w.ref]?.text||w.text}</p>{stories[w.ref]&&<small className="muted">{story.source==='ai'?'Fina · behavioural pattern agent, checked by the risk reviewer':'Fina · worked out from your numbers'}</small>}{stories[w.ref]&&<Feedback api={api} agent="analyzer" surface="outlook" source={story.source} label="Was this explanation clear?"/>}
        {w.advice.length>0&&<div className="warning-advice"><h4>Here's what would help</h4><AdviceList items={w.advice} source="calculation" api={api} surface="outlook"/></div>}</article>)}</div>
    </section>}
    {data.patterns.length>0&&!data.warnings.length&&<section className="panel"><p className="muted">These rises don't put any of your goals or your reserve at risk yet{data.has_forecast?'':' (create a forecast so I can check your reserve)'}. Worth keeping an eye on.</p></section>}
    {data.debts&&<Debts api={api} debts={data.debts} reload={reload} setPage={setPage}/>}
  </>;
}

/**
 * The Invest page: readiness checks, SIP amount, education and market headlines.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.setPage navigate
 * @returns Page element.
 * Used in: Invest.
 */
function Invest({api,setPage}){
  const [data,setData]=useState(null),[story,setStory]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState(''),[made,setMade]=useState({});
  useEffect(()=>{api('/api/v1/invest').then(setData).catch(e=>setError(e.message));},[]);
  async function explain(){setBusy(true);try{setStory(await api('/api/v1/invest/explain',{method:'POST'}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function makeGoal(g){await api('/api/v1/goals',{method:'POST',body:JSON.stringify(g)});setMade(m=>({...m,[g.name]:true}));}
  if(error)return <div className="error banner">{error}</div>;
  if(!data)return <div className="empty"><FinMark animated/>The investment agent is checking your readiness…</div>;
  const label={emergency_fund:'Emergency fund',costly_debt:'No costly debt',surplus:'Money left after goals'};
  const detail=c=>c.id==='emergency_fund'?(c.facts.months_covered==null?'Add your available cash in Your context to check.':`Your cash covers ${c.facts.months_covered} months of essentials (${money(c.facts.essentials_per_month)} a month); aim for ${c.facts.target_months}.`)
    :c.id==='costly_debt'?(c.passed?'No debts above 12% a year.':`${c.facts.debts.join(', ')} charge${c.facts.debts.length>1?'':'s'} more than ${c.facts.threshold_percent}% a year.`)
    :(c.facts.surplus_per_month==null?'Create a forecast on Overview so I can see your monthly surplus.':(c.facts.goal_needs_per_month?`About ${money(c.facts.surplus_per_month)} a month left after the ${money(c.facts.goal_needs_per_month)} your goals need.`:`About ${money(c.facts.surplus_per_month)} a month left, with no goal needing money right now.`));
  return <>
    <section className={`panel readiness ${data.ready?'ready':''}`}><div className="section-title"><div><span className="eyebrow">INVESTMENT AGENT</span><h2>{data.ready?'You look ready to start investing':'Not quite ready to invest yet'}</h2><p className="muted">{data.ready?`A cautious start: a SIP of ${money(data.sip_minor)} a month, half of what's left after your goals.`:'Getting these right first protects you from having to sell investments at a bad time.'}</p></div><PiggyBank size={30} className="muted"/></div>
      <div className="checks">{data.checks.map(c=><div key={c.id} className={`check ${c.passed?'pass':'fail'}`}><span>{c.passed?<Check size={16}/>:<X size={16}/>}</span><div><strong>{label[c.id]}</strong><p>{detail(c)}</p><p className="advice-why">Why it matters: {c.why}</p></div></div>)}</div>
      {data.advice.length>0&&<div className="warning-advice"><h4>Here's what I'd do</h4>{story?.items?.length?<AdviceList items={story.items} source={story.source} api={api}/>:<AdviceList items={data.advice} source="calculation" api={api}/>}<button className="secondary" disabled={busy} onClick={explain}><Sparkles size={14}/>{busy?'Fina is thinking…':'Explain with Fina'}</button>{story?.paused&&<p className="muted small-text">Fina's explanations are paused right now.</p>}</div>}
      {data.suggested_goals.length>0&&<div className="suggested"><h4>Make it a goal</h4>{data.suggested_goals.map(g=><div key={g.name} className="suggested-row"><span>{g.name}: {money(g.target_minor)} by {g.target_date}</span><button className="secondary" disabled={made[g.name]} onClick={()=>makeGoal(g)}>{made[g.name]?'Added to Goals':'Add as goal'}</button></div>)}</div>}
    </section>
    <div className="dashboard-grid">
      <section className="panel"><h2><LineChart size={18}/> Options to learn about</h2><p className="muted">General categories, not recommendations to buy. Research specific funds yourself or with a registered adviser.</p><div className="edu-list">{data.education.map(e=><div key={e.id} className="edu"><h3>{e.name}</h3><p><strong>Horizon:</strong> {e.horizon}</p><p><strong>Risk:</strong> {e.risk}</p><p className="advice-why">Why consider it: {e.why}</p></div>)}</div></section>
      <section className="panel"><h2><Newspaper size={18}/> Market headlines</h2><p className="muted">For context only. Headlines are not advice, and FinPilot never suggests buying or selling a specific stock.</p>{data.news.length?<ul className="news">{data.news.map(n=><li key={n.link}><a href={n.link} target="_blank" rel="noopener noreferrer">{n.title}<ExternalLink size={11}/></a><small className="muted">{n.source}{n.published?` · ${n.published.slice(0,16)}`:''}</small></li>)}</ul>:<p className="muted">No headlines right now.</p>}</section>
    </div>
    <p className="muted small-text disclaimer">FinPilot's investment agent offers education based on your own numbers, not personalised investment advice. Investments can lose value. For specific products, consult a SEBI-registered investment adviser.</p>
  </>;
}

/**
 * The Preferences page: opt-in sharing of de-identified examples that help test and improve Fina.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Page element.
 * Used in: Preferences (above Referral in the menu); GET/PUT /api/v1/preferences and DELETE .../contributions.
 */
function Preferences({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[saved,setSaved]=useState('');
  useEffect(()=>{api('/api/v1/preferences').then(setData).catch(e=>setError(e.message));},[]);
  async function save(next){setBusy(true);setError('');setSaved('');try{const r=await api('/api/v1/preferences',{method:'PUT',body:JSON.stringify({share_categories:next.share_categories,share_questions:next.share_questions})});setData(r);setSaved('Saved.');}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function withdraw(){if(!window.confirm('Delete everything you have shared and turn sharing off?'))return;setBusy(true);try{setData(await api('/api/v1/preferences/contributions',{method:'DELETE'}));setSaved('Everything you shared has been deleted.');}catch(e){setError(e.message);}finally{setBusy(false);}}
  if(!data)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Loading…</div>;
  const total=data.contributed.categorizer+data.contributed.coordinator;
  const toggle=(key,title,what)=><label className={`pref-toggle ${data[key]?'on':''}`}><input type="checkbox" checked={data[key]} disabled={busy} onChange={e=>save({...data,[key]:e.target.checked})}/><span className="switch" aria-hidden="true"/><span><strong>{title}</strong><small>{what}</small></span></label>;
  return <>
    <section className="panel pref-panel"><div className="section-title"><div><h2>Help make Fina better</h2><p className="muted">FinPilot tests its agents against example cases called goldens. Real examples make those tests far better. Sharing is off unless you turn it on, and you can withdraw at any time.</p></div><ShieldCheck className="muted"/></div>
      {toggle('share_categories','Share the categories I confirm',`The merchant line, its type and a rounded amount, with the category you chose. Used to test the categorizer. ${data.contributed.categorizer} shared so far.`)}
      {toggle('share_questions','Share the questions I ask Fina',`Your question and how Fina routed it, including any card it suggested. Used to test the coordinator. ${data.contributed.coordinator} shared so far.`)}
      {saved&&<p className="success-text">{saved}</p>}{error&&<p className="error" role="alert">{error}</p>}
    </section>
    <section className="panel pref-panel"><h2>What we do with it</h2>
      <ul className="pref-list">
        <li><Check size={14}/>Before anything is saved, links, emails, UPI handles, phone numbers and long reference numbers are removed.</li>
        <li><Check size={14}/>Shared examples are kept apart from your account data, under a code (never your name or username), only so they can be deleted if you withdraw.</li>
        <li><Check size={14}/>The team reviews examples before any become test cases; your balances, goals and other transactions are never shared.</li>
        <li><Check size={14}/>Turning a switch off deletes what you shared under it.</li>
      </ul>
      <p className="muted small-text">Please avoid putting people's names in questions you share.</p>
      <button className="secondary" disabled={busy||!total} onClick={withdraw}><Trash2 size={14}/>Withdraw and delete what I shared{total?` (${total})`:''}</button>
    </section>
  </>;
}

/**
 * The Referral page: FinaPoints, vouchers matched to spending, referral code, rules and achievement tokens.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Page element.
 * Used in: Referral (last in the menu).
 */
function Referral({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(''),[got,setGot]=useState(null),[copied,setCopied]=useState(false);
  async function load(){try{setData(await api('/api/v1/rewards'));}catch(e){setError(e.message);}}
  useEffect(()=>{load();},[]);
  async function redeem(v){setBusy(v.id);setError('');try{setGot(await api('/api/v1/rewards/redeem',{method:'POST',body:JSON.stringify({voucher_id:v.id,idempotency_key:crypto.randomUUID()})}));await load();}catch(e){setError(e.message);}finally{setBusy('');}}
  if(!data)return error?<div className="error banner">{error}</div>:<div className="empty"><FinMark animated/>Loading your FinaPoints…</div>;
  const link=`${window.location.origin}/?ref=${data.referral.code}`;
  const why=v=>!v.reason?null:v.reason.rising_percent!=null?`You spent ${money(v.reason.spent_minor)} on ${v.reason.category} in the last 30 days, up ${v.reason.rising_percent}%, so this saves on something you already buy.`:`You spent ${money(v.reason.spent_minor)} on ${v.reason.category} in the last 30 days, so this saves on something you already buy.`;
  return <>
    {error&&<div className="error banner" role="alert">{error}</div>}
    {got&&<div className="success banner" role="status"><Gift size={17}/>{got.title}: <code>{got.code}</code>. Demo voucher, no real value yet.</div>}
    <div className="stats"><div className="stat highlight"><span><Gift size={17}/>FinaPoints</span><strong>{data.balance.toLocaleString('en-IN')}</strong><small>Worth about {money(data.worth_minor)} in vouchers · {data.rules.points.toLocaleString('en-IN')} points = ₹{data.rules.rupees}</small></div><div className="stat"><span><Target size={17}/>Earned so far</span><strong>{data.earned_total.toLocaleString('en-IN')}</strong><small>From proven goals, friends and achievements</small></div><div className="stat"><span><Sparkles size={17}/>Friends joined</span><strong>{data.referral.friends_joined}</strong><small>+{data.rules.referral} points each</small></div></div>
    <div className="dashboard-grid">
      <section className="panel"><div className="section-title"><div><h2>Vouchers for you</h2><p className="muted">Picked from your behavioural patterns: what you actually spend on comes first.</p></div><span className="badge">DEMO</span></div>
        <div className="voucher-list">{data.vouchers.map(v=><div key={v.id} className={`voucher ${v.recommended?'recommended':''}`}><div><h3>{v.title}</h3><p className="muted">{v.points.toLocaleString('en-IN')} FinaPoints</p>{why(v)&&<p className="voucher-why">{why(v)}</p>}</div><button className={v.affordable?'primary':'secondary'} disabled={!v.affordable||busy===v.id} onClick={()=>redeem(v)}>{busy===v.id?'Redeeming…':v.affordable?'Redeem':`${(v.points-data.balance).toLocaleString('en-IN')} more`}</button></div>)}</div>
        <p className="muted small-text">Vouchers are demonstrations with no monetary value until a voucher partner is connected.</p>
      </section>
      <section className="panel"><span className="eyebrow">INVITE FRIENDS</span><h2>Your referral code</h2><div className="ref-code"><code>{data.referral.code}</code><button className="secondary" onClick={()=>{navigator.clipboard?.writeText(link);setCopied(true);setTimeout(()=>setCopied(false),2000);}}><Copy size={14}/>{copied?'Copied':'Copy invite link'}</button></div>
        <p className="muted small-text">Friends who sign up with your code get {data.rules.welcome} points and you get {data.rules.referral}.</p>
        <h3 className="earn-title">How to earn</h3><ul className="earn-list"><li><strong>+{data.rules.goal.toLocaleString('en-IN')}</strong> when you reach a goal: tick it on Goals, and once transfers into savings made after you set the goal cover the full amount, the points arrive</li><li>Points are never taken back, and your balance never drops below zero</li><li><strong>+{data.rules.referral}</strong> for each friend who joins with your code</li><li><strong>+{data.rules.welcome}</strong> for joining with a friend's code</li></ul></section>
    </div>
    <section className="panel"><div className="section-title"><div><h2><Award size={18}/> Achievements</h2><p className="muted">Collect tokens for real progress. Each comes with a one-time FinaPoints bonus.</p></div><span className="badge">{data.achievements.filter(a=>a.earned).length}/{data.achievements.length} UNLOCKED</span></div>
      <div className="tokens">{data.achievements.map(a=><div key={a.id} className={`token-card ${a.earned?'earned':'locked'}`}><div className={`token ${a.tier}`}><div className="token-face">{a.earned?<Award size={30}/>:<Lock size={24}/>}</div></div><h3>{a.title}</h3><p>{a.about}</p><small>{a.earned?(a.bonus?`+${a.bonus} FinaPoints`:'Unlocked'):`${a.progress}/${a.target}`}</small></div>)}</div>
    </section>
    {data.redeemed.length>0&&<section className="panel"><h2>Your vouchers</h2><table><thead><tr><th>Voucher</th><th>Code</th><th>Date</th><th className="right">Points</th></tr></thead><tbody>{data.redeemed.map(r=><tr key={r.code}><td>{r.title}</td><td><code>{r.code}</code></td><td className="muted">{r.date}</td><td className="right">{r.points.toLocaleString('en-IN')}</td></tr>)}</tbody></table></section>}
    <section className="panel"><h2>Points history</h2>{data.history.length?<table><thead><tr><th>Date</th><th>What</th><th className="right">Points</th></tr></thead><tbody>{data.history.map((h,i)=><tr key={i}><td className="muted">{h.date}</td><td>{h.label}</td><td className={`right ${h.points>0?'positive':''}`}>{h.points>0?'+':''}{h.points.toLocaleString('en-IN')}</td></tr>)}</tbody></table>:<p className="muted">No points yet. Reach a goal or invite a friend to start earning.</p>}</section>
  </>;
}

/**
 * Agent activity: each agent's step from transactions to forecast, with timings and feedback.
 *
 * @param props.txs transactions
 * @param props.total transaction count
 * @param props.run forecast run
 * @param props.busy busy flag
 * @param props.retryAI retry categorization
 * @param props.setPage navigate
 * @param props.limit AI requests left today
 * @param props.aiReady AI available
 * @param props.profile profile
 * @param props.askFina open Fina with a question
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Page element.
 * Used in: Agent activity.
 */
function Workflow({txs,total,run,busy,retryAI,setPage,limit,aiReady,profile,askFina,api}){
  const stageAgent={'Ingestion':'ingestion','Categorizer':'categorizer','Investigator':'investigator','Behavioural pattern agent':'analyzer','Forecaster':'forecaster','Numeric checks':'numeric_checks','Advisor + risk reviewer':'advisor','Goal tracker':'goals'};
  const count=source=>txs.filter(t=>t.category_source===source).length, unresolved=count('unresolved');
  const retryable=aiReady&&limit>0?txs.filter(t=>t.category_source==='unresolved').length:0;
  const unique=key=>[...new Set(txs.map(t=>t.category_provenance?.[key]).filter(Boolean))].join(', ');
  const models=unique('model'), prompts=unique('prompt_version');
  const taught=new Set(txs.filter(t=>t.category_source==='owner'||t.category_note).map(t=>t.description.toLowerCase().trim())).size;
  const followed=txs.filter(t=>t.category_source==='model'&&/owner/i.test(t.category_reason||'')).length;
  const forecastSteps=run?run.steps.map(s=>({name:stepNames[s.node]||s.node,kind:'Calculation',status:'done',details:[...(s.details||[`${s.method} · ${s.status}`]),...(s.node==='analyzer'?['Also watches for spending that keeps rising and what it would mean for your goals: see Outlook.']:[])],time:s.duration_ms})):Object.values(stepNames).map(name=>({name,kind:'Calculation',status:'waiting',details:['Runs when you create a forecast on Overview.']}));
  const stages=[
    {name:'Ingestion',kind:'Validation',status:total?'done':'waiting',details:total?[`${total} transactions checked for date, amount, sign and currency, then stored in your ledger.`]:['Add or import transactions to begin.']},
    {name:'Categorizer',kind:models?`AI · ${models}`:'AI',status:!total?'waiting':unresolved?'attention':'done',details:!total?['Runs when transactions are added.']:[`${count('model')} categorized by AI${prompts?` with prompt ${prompts}`:''}.`,`${count('owner')} labeled by you.`,taught?`Learning from ${Math.min(taught,30)} of your labels and notes${followed?`; ${followed} later transactions followed them`:''}.`:'Learns from your labels and notes as you fix categories.',...(count('merchant_rule')?[`${count('merchant_rule')} labeled by an older rule.`]:[]),unresolved?`${unresolved} still uncategorized${aiReady&&''}.`:'Every transaction has a category.',`${limit} AI categorization requests left today. Your labels apply to the same merchant everywhere.`],action:unresolved?<div className="button-row">{retryable>0&&<button className="secondary" disabled={busy} onClick={retryAI}><Sparkles size={15}/>Retry with AI ({retryable})</button>}<button className="text-button" onClick={()=>setPage('transactions')}>Fix them one by one<ArrowRight size={14}/></button></div>:null},
    {name:'Investigator',kind:'AI',status:!profile?'waiting':missingFacts(profile).length?'attention':'done',details:!profile?['Runs once your context is saved.']:missingFacts(profile).length?[`Your forecast is missing ${missingFacts(profile).join(', ')}.`,'Fina asks about these and suggests regular income and bills from your history; nothing is saved until you confirm.']:['Your context has what the forecast needs.'],action:profile&&missingFacts(profile).length?<div className="button-row"><button className="secondary" onClick={askFina}><Sparkles size={15}/>Answer Fina's questions</button></div>:null},
    ...forecastSteps,
    {name:'Advisor + risk reviewer',kind:'Calculation + AI',status:'done',details:['Works out options with exact effects: spending less on a discretionary category, paying later, a smaller amount, completing your plan, or a later goal date. Essentials are never suggested as cuts.','Fina explains the best options; the risk reviewer must approve each answer before you see it. Admins can pause suggestions.'],action:null},
    {name:'Goal tracker',kind:'Calculation + AI',status:'done',details:['Calculates progress, the monthly amount each goal needs and your pace from the latest forecast.','On request, Fina writes a short note about a goal; its numbers come only from that calculation.'],action:<div className="button-row"><button className="text-button" onClick={()=>setPage('goals')}>Open Goals<ArrowRight size={14}/></button></div>}];
  return <section className="panel"><div className="section-title"><div><h2>From transactions to a trajectory</h2><p className="muted">Each agent in order: what it used and what it produced. The categorizer runs as transactions arrive; the calculations run when you create a forecast. In Fina's chat, the coordinator routes each message to the right agent.</p></div><span className="badge">{run?'FORECAST RUN COMPLETE':'NO FORECAST YET'}</span></div>
    <ol className="workflow">{stages.map((s,i)=><li className={`stage ${s.status}`} key={s.name}><span className="stage-num">{s.status==='done'?<Check size={15}/>:i+1}</span><div className="stage-body"><div className="stage-head"><h3>{s.name}</h3><span className="stage-kind">{s.kind}</span>{s.time!=null&&<small>{s.time} ms</small>}<span className="stage-status">{s.status==='done'?'Done':s.status==='attention'?'Needs attention':'Waiting'}</span></div><ul>{s.details.map(d=><li key={d}>{d}</li>)}</ul>{s.action}{s.status!=='waiting'&&stageAgent[s.name]&&<Feedback api={api} agent={stageAgent[s.name]} surface="agent_activity" itemRef={`stage-${stageAgent[s.name]}-${run?.id||'ledger'}`} label="Did this step look right?"/>}</div></li>)}</ol>
    {run&&<><p className="muted">{run.analysis.transaction_count} source transactions · source version {run.source_version} · prompt catalog {run.prompt_release}</p><details><summary>Inspect transaction references</summary><ul className="refs">{run.analysis.evidence_refs.map(id=><li key={id}><code>{id}</code></li>)}</ul></details></>}
    {plannedAgents.length>0&&<><div className="section-title small"><div><h3>Coming later</h3><p className="muted">These agents have versioned prompts. Each needs a supporting feature before it can run.</p></div></div><div className="planned">{plannedAgents.map(([name,job,partly])=><div key={name} className={partly?'partly':''}><h4>{name}</h4><p>{partly||job}</p><span className="badge">{partly?'ACTIVE IN FINA CHAT':'NOT ACTIVE YET'}</span></div>)}</div></>}</section>;
}

const feedbackReasons=[['wrong_numbers','Wrong numbers'],['not_relevant','Not relevant'],['confusing','Confusing'],['tone','Tone'],['too_slow','Too slow'],['other','Other']];
const agentNames={ingestion:'Ingestion',categorizer:'Categorizer',investigator:'Investigator',analyzer:'Behavioural pattern agent',forecaster:'Forecaster · Fina answers',numeric_checks:'Numeric checks',advisor:'Advisor',goals:'Goal tracker',coordinator:'Coordinator',risk:'Risk reviewer',transcriber:'Voice transcriber'};
/**
 * Which agent answered a chat route, for feedback.
 *
 * @param r route name
 * @returns Agent name.
 * Used in: Feedback on chat answers.
 */
const routeAgent=r=>({explain:'forecaster',purchase:'forecaster',advice:'advisor',category:'categorizer',upcoming_flow:'investigator',profile_value:'investigator',setup:'investigator',goal:'goals',goal_status:'goals',goal_saving:'goals',goal_saving_ask:'goals',outlook:'analyzer'}[r]||'coordinator');
/**
 * Normalize an answer's source for feedback.
 *
 * @param s source
 * @returns 'ai', 'calculation' or null.
 * Used in: Feedback.
 */
const feedbackSource=s=>s==='ai'||s==='calculation'?s:null;

/**
 * Thumbs up or down (with a reason when down) on one output.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.agent agent rated
 * @param props.surface where in the app
 * @param props.source 'ai' or 'calculation'
 * @param props.itemRef id of the output
 * @param props.label prompt text
 * @returns Inline element.
 * Used in: Chat, advice, notes, outlook; Admin > Feedback.
 */
function Feedback({api,agent,surface,source,itemRef,label='Helpful?'}){
  const ref=useRef(itemRef||crypto.randomUUID().replaceAll('-','')),[vote,setVote]=useState(0),[reason,setReason]=useState(null),[failed,setFailed]=useState(false);
  async function send(rating,why=null){setVote(rating);setReason(why);setFailed(false);try{await api('/api/v1/feedback',{method:'POST',body:JSON.stringify({item_ref:ref.current,agent,surface,source:feedbackSource(source),rating,reason:why})});}catch{setFailed(true);}}
  return <div className="feedback"><span>{label}</span><button type="button" aria-label="Thumbs up" aria-pressed={vote===1} className={vote===1?'on up':''} onClick={()=>send(1)}><ThumbsUp size={13}/></button><button type="button" aria-label="Thumbs down" aria-pressed={vote===-1} className={vote===-1?'on down':''} onClick={()=>send(-1,reason)}><ThumbsDown size={13}/></button>
    {vote===-1&&<div className="fb-reasons" role="group" aria-label="What was wrong?">{feedbackReasons.map(([k,t])=><button type="button" key={k} className={reason===k?'on':''} onClick={()=>send(-1,k)}>{t}</button>)}</div>}
    {vote!==0&&<small>{failed?'Could not save':'Thanks!'}</small>}</div>;
}

/**
 * A list of advice items with their "Why" lines and feedback.
 *
 * @param props.items advice items
 * @param props.source 'ai' or 'calculation'
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.surface feedback surface
 * @returns List element.
 * Used in: Advice across the app.
 */
function AdviceList({items,source,api,surface='advice'}){
  return <div className="advice-list">{items.map(i=><div key={i.ref} className="advice-item"><strong>{i.title}</strong><p>{i.text}</p>{i.why&&<p className="advice-why">{i.why}</p>}</div>)}<small className="muted">{source==='ai'?'Fina · options worked out from your numbers, checked by the risk reviewer':'Fina · options worked out from your numbers'}. Your choice, always.</small>{api&&<Feedback api={api} agent="advisor" surface={surface} source={source} label="Were these options useful?"/>}</div>;
}

/**
 * "Show me options" for the current forecast or purchase.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.run forecast run
 * @param props.scenarioInput purchase or null
 * @returns Button and results.
 * Used in: Overview.
 */
function OptionsButton({api,run,scenarioInput}){
  const [result,setResult]=useState(null),[busy,setBusy]=useState(false),[error,setError]=useState('');
  useEffect(()=>{setResult(null);setError('');},[scenarioInput?.amount_minor,scenarioInput?.date]);
  async function ask(){setBusy(true);setError('');try{setResult(await api('/api/v1/advisor/advice',{method:'POST',body:JSON.stringify({baseline_id:run.id,question:'What could make this work?',scenario:scenarioInput})}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  if(result?.paused)return <p className="muted small-text">Suggestions are paused right now.</p>;
  if(result)return result.items.length?<AdviceList items={result.items} source={result.source} api={api}/>:<p className="muted small-text">I don't see an option that would change this picture.</p>;
  return <><button className="secondary options-btn" disabled={busy} onClick={ask}><Sparkles size={14}/>{busy?'Fina is working out options…':'What could make this work?'}</button>{error&&<p className="error">{error}</p>}</>;
}

// Voice uses the browser's Web Speech API: Chrome and Edge send the audio to their own speech service to turn it into text.
const Recognition = typeof window!=='undefined' && (window.SpeechRecognition||window.webkitSpeechRecognition);
const canSpeak = typeof window!=='undefined' && 'speechSynthesis' in window;
/**
 * Text made pleasant to read aloud: ISO dates as "7 November 2026", ₹ amounts as "35,000 rupees".
 *
 * @param text reply text
 * @returns Text for speech.
 * Used in: speak().
 */
const spoken = text => text.replace(/(\d{4})-(\d{2})-(\d{2})/g,(_,y,m,d)=>new Date(+y,m-1,+d).toLocaleDateString('en-IN',{day:'numeric',month:'long',year:'numeric'})).replace(/₹\s?([\d,]+(?:\.\d+)?)/g,'$1 rupees');
/**
 * Read text aloud with an Indian-English voice when installed (browser speech synthesis).
 *
 * @param text text to speak
 * @returns Nothing.
 * Used in: Fina's spoken replies and voice cards.
 */
function speak(text){
  if(!canSpeak||!text)return;
  const s=window.speechSynthesis,u=new SpeechSynthesisUtterance(spoken(text)),voices=s.getVoices();
  u.voice=voices.find(v=>v.lang==='en-IN')||voices.find(v=>v.lang?.startsWith('en'))||null;u.lang=u.voice?.lang||'en-IN';u.rate=1.02;
  s.cancel();s.speak(u);
}
const voiceErrors={'not-allowed':'Microphone access is blocked. Allow it in the address bar and try again.','service-not-allowed':'Microphone access is blocked. Allow it in the address bar and try again.','no-speech':"I didn't catch anything. Tap the mic and try again.",'audio-capture':'No microphone was found.',network:'Voice needs an internet connection in this browser.'};
// When the server has a speech model, the mic records a short clip and the server turns it into text;
// otherwise (or after a server failure) the browser's own recognition is used.
let voiceServer=null;
/**
 * Whether the server has a speech model (checked once).
 * @returns Promise of true or false.
 * Used in: useVoice().
 */
const serverVoice=()=>voiceServer??=fetch('/health').then(r=>r.json()).then(h=>!!h.voice_enabled).catch(()=>false);
const canRecord=typeof navigator!=='undefined'&&!!navigator.mediaDevices?.getUserMedia&&typeof window.MediaRecorder!=='undefined';
/**
 * Convert a recorded clip to 16 kHz mono WAV, base64-encoded, for server transcription.
 *
 * @param blob MediaRecorder audio
 * @returns Promise of a base64 string.
 * Used in: useVoice() in server mode.
 */
async function toWav(blob){
  const ctx=new AudioContext(),decoded=await ctx.decodeAudioData(await blob.arrayBuffer());ctx.close();
  const rate=16000,off=new OfflineAudioContext(1,Math.ceil(decoded.duration*rate),rate),src=off.createBufferSource();
  src.buffer=decoded;src.connect(off.destination);src.start();
  const pcm=(await off.startRendering()).getChannelData(0),view=new DataView(new ArrayBuffer(44+pcm.length*2));
  const text=(at,s)=>[...s].forEach((c,i)=>view.setUint8(at+i,c.charCodeAt(0)));
  text(0,'RIFF');view.setUint32(4,36+pcm.length*2,true);text(8,'WAVE');text(12,'fmt ');view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,rate,true);view.setUint32(28,rate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,'data');view.setUint32(40,pcm.length*2,true);
  pcm.forEach((v,i)=>view.setInt16(44+i*2,Math.max(-1,Math.min(1,v))*0x7fff,true));
  const bytes=new Uint8Array(view.buffer);let bin='';
  for(let i=0;i<bytes.length;i+=0x8000)bin+=String.fromCharCode(...bytes.subarray(i,i+0x8000));
  return btoa(bin);
}
/**
 * Hook for the mic: server transcription when available, else the browser's own recognition.
 *
 * @param onFinal called with the final transcript
 * @param api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns {supported, listening, interim, error, toggle}.
 * Used in: Fina's chat mic and the voice cards.
 */
function useVoice(onFinal,api){
  const [listening,setListening]=useState(false),[interim,setInterim]=useState(''),[error,setError]=useState(''),[server,setServer]=useState(false);
  const rec=useRef(null),handler=useRef(onFinal);handler.current=onFinal;
  useEffect(()=>{let live=true;if(api&&canRecord)serverVoice().then(v=>live&&setServer(v));return()=>{live=false;rec.current?.abort();};},[]);
  async function record(){
    let stream;
    try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true}});}catch{setError(voiceErrors['not-allowed']);return;}
    const chunks=[],mr=new MediaRecorder(stream),ctx=new AudioContext(),meter=ctx.createAnalyser(),samples=new Uint8Array(meter.fftSize);
    ctx.createMediaStreamSource(stream).connect(meter);
    let heard=false,cancelled=false;const started=Date.now();let quietSince=started;
    // Stop on its own after a pause in speech, at 20 seconds, or if nothing is said for 7 seconds.
    const tick=setInterval(()=>{meter.getByteTimeDomainData(samples);const rms=Math.sqrt(samples.reduce((s,v)=>s+((v-128)/128)**2,0)/samples.length);
      if(rms>0.03){heard=true;quietSince=Date.now();}
      if((heard&&Date.now()-quietSince>1400)||Date.now()-started>20000||(!heard&&Date.now()-started>7000))stop();},100);
    function stop(){clearInterval(tick);if(mr.state!=='inactive')mr.stop();}
    mr.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    mr.onstop=async()=>{
      stream.getTracks().forEach(t=>t.stop());ctx.close();rec.current=null;
      const done=(message='')=>{setListening(false);setInterim('');if(message)setError(message);};
      if(cancelled)return done();
      if(!heard)return done(voiceErrors['no-speech']);
      setInterim('Working out what you said…');
      try{const r=await api('/api/v1/voice/transcribe',{method:'POST',body:JSON.stringify({audio_base64:await toWav(new Blob(chunks,{type:mr.mimeType}))})});
        done(r.text?'':voiceErrors['no-speech']);if(r.text)handler.current(r.text);}
      catch(e){if(Recognition)setServer(false);done(`${e.message}${Recognition?' Tap the mic again to use your browser\'s voice recognition.':''}`);}
    };
    rec.current={stop,abort(){cancelled=true;stop();}};
    setError('');setListening(true);setInterim('');mr.start();
  }
  function toggle(){
    if(listening){rec.current?.stop();return;}
    if(canSpeak)window.speechSynthesis.cancel();
    if(server)return record();
    const r=new Recognition();r.lang='en-IN';r.interimResults=true;r.continuous=false;
    let final='';
    r.onresult=e=>{const all=Array.from(e.results);final=all.filter(x=>x.isFinal).map(x=>x[0].transcript).join(' ');setInterim(all.map(x=>x[0].transcript).join(' '));};
    r.onerror=e=>{if(e.error!=='aborted')setError(voiceErrors[e.error]||'Voice input stopped. Try again or type instead.');};
    r.onend=()=>{setListening(false);setInterim('');rec.current=null;if(final.trim())handler.current(final.trim());};
    setError('');setListening(true);rec.current=r;
    try{r.start();}catch{setListening(false);}
  }
  return {supported:server||!!Recognition,listening,interim,error,toggle};
}
/**
 * The mic button, red and pulsing while listening.
 *
 * @param props.voice from useVoice()
 * @param props.label accessible label
 * @returns Button or nothing when voice is unsupported.
 * Used in: Chat and voice cards.
 */
function MicButton({voice,label='Speak to Fina'}){
  if(!voice.supported)return null;
  return <button type="button" className={`icon-button mic ${voice.listening?'listening':''}`} aria-pressed={voice.listening} aria-label={voice.listening?'Stop listening':label} title={voice.listening?'Stop listening':label} onClick={voice.toggle}>{voice.listening?<MicOff size={16}/>:<Mic size={16}/>}</button>;
}
/**
 * A "Tell Fina" voice card: speak, see what was heard and confirm Fina's prefilled card.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.profile profile
 * @param props.currency ISO currency code, e.g. INR
 * @param props.onChanged refresh after a confirm
 * @param props.setPage navigate
 * @param props.openQuestions open Fina's questions
 * @param props.openReroute open the re-plan
 * @param props.title card title
 * @param props.examples example phrases
 * @param props.page 'goals' or 'context', sent to the server
 * @returns Panel or nothing.
 * Used in: Goals and Your context.
 */
function VoiceAsk({api,profile,currency,onChanged,setPage,openQuestions,openReroute,title,examples,page}){
  const [heard,setHeard]=useState(''),[reply,setReply]=useState(null),[busy,setBusy]=useState(false);
  const voice=useVoice(send,api);
  async function send(question){
    setHeard(question);setReply(null);setBusy(true);
    try{const r=await api('/api/v1/assistant/message',{method:'POST',body:JSON.stringify({question,baseline_id:null,scenario:null,history:[],page:page||null})});setReply(r);speak(r.answer);}
    catch(e){setReply({answer:e.message,source:'error'});}
    finally{setBusy(false);}
  }
  if(!voice.supported||!profile)return null;
  return <section className="panel voice-ask"><div className="voice-head"><MicButton voice={voice} label={title}/><div><strong>{voice.listening?'Listening…':title}</strong><p className="muted">{voice.listening?(voice.interim||'Go ahead, I\'m listening.'):<>Tap the mic and say it, for example {examples.map((x,i)=><span key={x}>{i?' or ':''}“{x}”</span>)}. Nothing is saved until you confirm.</>}</p></div></div>
    {voice.error&&<small className="error-text">{voice.error}</small>}
    {heard&&!voice.listening&&<div className="voice-turn"><p className="voice-heard">You said: “{heard}”</p>
      {busy?<p className="muted">Fina is working it out…</p>:reply&&<div className={`chat-msg assistant ${reply.source==='error'?'error-msg':''}`}>{reply.answer}{reply.action&&<ChatAction action={reply.action} api={api} profile={profile} currency={currency} onChanged={onChanged} openQuestions={openQuestions} goOverview={()=>setPage('overview')} goGoals={()=>setPage('goals')} goOutlook={()=>setPage('outlook')} goInvest={()=>setPage('invest')} goReroute={openReroute}/>}</div>}</div>}
  </section>;
}

/**
 * The card under a Fina reply: confirm a goal, flow, value, saving or category, or a link to a page.
 *
 * @param props.action action from the server
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.profile profile
 * @param props.currency ISO currency code, e.g. INR
 * @param props.onChanged refresh after a confirm
 * @param props.openQuestions open questions
 * @param props.goOverview / goGoals / goOutlook / goInvest navigate
 * @param props.goReroute open the re-plan
 * @returns Card element.
 * Used in: Fina's chat and voice cards.
 */
function ChatAction({action,api,profile,currency,onChanged,openQuestions,goOverview,goGoals,goOutlook,goInvest,goReroute}){
  const [state,setState]=useState(action.type==='confirm_goal_saving'?{amount:String(action.add_minor/100)}:action.type==='confirm_flow'?{label:action.flow.label||'',amount:action.flow.amount_minor?String(Math.abs(action.flow.amount_minor)/100):'',date:action.flow.date||'',direction:action.flow.direction||(action.flow.amount_minor<0?'out':'in')}:action.type==='confirm_value'?{amount:String(action.amount_minor/100)}:action.type==='confirm_goal'?{label:action.goal.name||'',amount:action.goal.target_minor?String(action.goal.target_minor/100):'',date:action.goal.target_date||''}:{});
  const [status,setStatus]=useState(''),[busy,setBusy]=useState(false);
  async function saveProfile(next){const v=(await api('/api/v1/profile')).version;await api('/api/v1/profile',{method:'PUT',body:JSON.stringify({profile:next,expected_version:v})});}
  async function confirm(){setBusy(true);setStatus('');try{
    if(action.type==='confirm_flow')await saveProfile({...profile,scheduled_flows:[...profile.scheduled_flows,{label:state.label.trim()||'Upcoming payment',amount_minor:(state.direction==='out'?-1:1)*Math.abs(amount(state.amount)),date:state.date}]});
    if(action.type==='confirm_value')await saveProfile({...profile,[action.field==='reserve'?'reserve_minor':'opening_balance_minor']:amount(state.amount)});
    if(action.type==='confirm_goal_saving')await api(`/api/v1/goals/${action.goal_id}`,{method:'PATCH',body:JSON.stringify({saved_minor:action.saved_minor+Math.max(0,amount(state.amount)||0)})});
    if(action.type==='confirm_goal')await api('/api/v1/goals',{method:'POST',body:JSON.stringify({name:state.label.trim()||'My goal',target_minor:amount(state.amount),target_date:state.date})});
    if(action.type==='confirm_category'){let v=(await api('/api/v1/profile')).version;for(const id of action.ids){v=(await api(`/api/v1/transactions/${id}/category`,{method:'POST',body:JSON.stringify({expected_version:v,category:action.category})})).version;}}
    setStatus('done');await onChanged(action.type);}catch(e){setStatus(e.message);}finally{setBusy(false);}}
  if(action.type==='open_questions')return <button className="secondary chat-act" onClick={openQuestions}><Sparkles size={14}/>Answer Fina's questions</button>;
  if(action.type==='advice')return <AdviceList items={action.items} source="ai" api={api} surface="chat"/>;
  if(action.type==='go_invest')return <button className="secondary chat-act" onClick={goInvest}><LineChart size={14}/>Open Invest</button>;
  if(action.type==='go_reroute')return <button className="secondary chat-act" onClick={()=>goReroute(action.amount_minor)}><LifeBuoy size={14}/>Re-plan my goals</button>;
  if(action.type==='go_outlook')return <button className="secondary chat-act" onClick={goOutlook}><TriangleAlert size={14}/>Open Outlook</button>;
  if(action.type==='go_goals')return <button className="secondary chat-act" onClick={goGoals}><Target size={14}/>Open Goals</button>;
  if(action.type==='go_overview')return <button className="secondary chat-act" onClick={goOverview}>Go to Overview<ArrowRight size={14}/></button>;
  if(action.type==='scenario')return <small className="chat-note">Simulated {money(action.amount_minor,currency)} on {action.date}. Try other amounts in What if.</small>;
  if(status==='done')return <small className="chat-note done"><Check size={13}/>{action.type==='confirm_category'?'Labelled, and I\'ll remember it.':action.type==='confirm_goal_saving'?<>Added to {action.name}. <button className="text-button inline" onClick={goGoals}>See it on Goals</button></>:action.type==='confirm_goal'?<>Goal added. <button className="text-button inline" onClick={goGoals}>See it on Goals</button></>:'Saved. Refresh your forecast on Overview to include it.'}</small>;
  return <div className="chat-card">
    {(action.type==='confirm_flow'||action.type==='confirm_goal')&&<div className="chat-fields"><input aria-label="Label" placeholder={action.type==='confirm_goal'?'What for, e.g. Goa trip':'What is it, e.g. Salary'} value={state.label} maxLength="80" onChange={e=>setState({...state,label:e.target.value})}/><input aria-label={`Amount (${currency})`} type="number" min="0" step=".01" placeholder={`Amount (${currency})`} value={state.amount} onChange={e=>setState({...state,amount:e.target.value})}/><input aria-label="Date" type="date" min={profile.as_of} value={state.date} onChange={e=>setState({...state,date:e.target.value})}/>{action.type==='confirm_flow'&&<div className="segmented" role="group" aria-label="Money in or out"><button type="button" className={state.direction==='in'?'active':''} aria-pressed={state.direction==='in'} onClick={()=>setState({...state,direction:'in'})}>Money in</button><button type="button" className={state.direction==='out'?'active':''} aria-pressed={state.direction==='out'} onClick={()=>setState({...state,direction:'out'})}>Money out</button></div>}</div>}
    {action.type==='confirm_goal_saving'&&<div className="chat-fields one"><input aria-label={`Amount for ${action.name} (${currency})`} type="number" min="0" step=".01" value={state.amount} onChange={e=>setState({...state,amount:e.target.value})}/></div>}
    {action.type==='confirm_value'&&<div className="chat-fields one"><input aria-label={action.field==='reserve'?'Reserve':'Available cash'} type="number" step=".01" value={state.amount} onChange={e=>setState({...state,amount:e.target.value})}/></div>}
    {action.type==='confirm_category'&&<p className="chat-card-text">{action.ids.length} transaction{action.ids.length===1?'':'s'} matching “{action.merchant}” → <span className="category">{action.category}</span></p>}
    <button className="primary" disabled={busy||((action.type==='confirm_goal'||action.type==='confirm_flow')&&!(state.label.trim()&&Number(state.amount)>0&&state.date))} onClick={confirm}><Check size={14}/>{busy?'Saving…':action.type==='confirm_goal_saving'?`Add to ${action.name}`:action.type==='confirm_goal'?'Add goal':action.type==='confirm_flow'?'Add to my plan':action.type==='confirm_value'?'Save':'Label them'}</button>
    {status&&status!=='done'&&<small className="error-text">{status}</small>}
  </div>;
}

/**
 * Fina's chat: messages, suggestions, mic, spoken replies, mute and feedback.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.run forecast run
 * @param props.scenarioInput purchase being discussed
 * @param props.currency ISO currency code, e.g. INR
 * @param props.setPage navigate
 * @param props.profile profile
 * @param props.onChanged refresh after confirms
 * @param props.openQuestions open questions
 * @param props.openReroute open the re-plan
 * @returns Floating chat.
 * Used in: Every customer page (bottom right).
 */
function ChatWidget({api,run,scenarioInput,currency,setPage,profile,onChanged,openQuestions,openReroute}){
  const [open,setOpen]=useState(false),[messages,setMessages]=useState([]),[text,setText]=useState(''),[busy,setBusy]=useState(false);
  const [muted,setMuted]=useState(()=>{try{return localStorage.getItem('fina-muted')==='1';}catch{return false;}});
  const end=useRef(null),voice=useVoice(q=>ask(q,true),api);
  function toggleMute(){const next=!muted;setMuted(next);if(next&&canSpeak)window.speechSynthesis.cancel();try{localStorage.setItem('fina-muted',next?'1':'0');}catch{}}
  useEffect(()=>{if(!open&&canSpeak)window.speechSynthesis.cancel();},[open]);
  useEffect(()=>{end.current?.scrollIntoView({block:'end'});},[messages,busy,open]);
  const about=scenarioInput?`a ${money(scenarioInput.amount_minor,currency)} purchase on ${scenarioInput.date}`:'your money';
  const suggestions=[...(scenarioInput?["Why can't I afford this?"]:["Can I afford a 2,000 dinner on Friday?"]),'Why is my balance going down?',"What's missing from my plan?"];
  async function ask(question,byVoice=false){
    question=question.trim();if(!question||busy)return;
    const history=messages.slice(-6).map(m=>({role:m.role,text:m.text.slice(0,1200)}));
    setMessages(m=>[...m,{role:'owner',text:question,voice:byVoice}]);setText('');setBusy(true);
    try{const r=await api('/api/v1/assistant/message',{method:'POST',body:JSON.stringify({question,baseline_id:run?.id||null,scenario:scenarioInput||null,history})});setMessages(m=>[...m,{role:'assistant',text:r.answer,source:r.source,action:r.action,route:r.route}]);if(byVoice&&!muted)speak(r.answer);}
    catch(e){setMessages(m=>[...m,{role:'assistant',text:e.message,source:'error'}]);}
    finally{setBusy(false);}
  }
  return <div className="chat">{open&&<section className="chat-panel" role="dialog" aria-label="Chat with Fina"><header><span className="fina-avatar" aria-hidden="true">F</span><div><strong>Fina</strong><small>Your money companion · talking about {about}</small></div>{canSpeak&&<button className="icon-button" aria-pressed={!muted} aria-label={muted?'Let Fina speak replies':'Mute Fina'} title={muted?'Let Fina speak replies':'Mute Fina'} onClick={toggleMute}>{muted?<VolumeX size={17}/>:<Volume2 size={17}/>}</button>}<button className="icon-button" aria-label="Close chat" onClick={()=>setOpen(false)}><X size={17}/></button></header>
    <div className="chat-body">{!profile?<div className="chat-msg assistant">Hi, I'm Fina. Save your context first and I'll help from there.<button className="text-button" onClick={()=>{setPage('context');setOpen(false);}}>Go to Your context<ArrowRight size={14}/></button></div>:<>
      <div className="chat-msg assistant">Hi, I'm Fina. {scenarioInput?`Thinking about ${about}? Ask me anything about it.`:"Ask about your forecast, check a purchase, tell me about your salary or bills, or teach me a category."} The decisions always stay yours.</div>
      {messages.map((m,i)=><div key={i} className={`chat-msg ${m.role} ${m.source==='error'?'error-msg':''}`}>{m.text}{m.action&&<ChatAction action={m.action} api={api} profile={profile} currency={currency} onChanged={onChanged} openQuestions={()=>{openQuestions();setOpen(false);}} goOverview={()=>{setPage('overview');setOpen(false);}} goGoals={()=>{setPage('goals');setOpen(false);}} goOutlook={()=>{setPage('outlook');setOpen(false);}} goInvest={()=>{setPage('invest');setOpen(false);}} goReroute={a=>{openReroute(a);setOpen(false);}}/>}{m.role==='owner'&&m.voice&&<small className="said"><Mic size={11}/>Spoken</small>}{m.role==='assistant'&&m.source!=='error'&&<small>{m.source==='ai'?'Fina · checked against your numbers':'Fina · worked out from your forecast'}{canSpeak&&<button className="text-button inline listen" aria-label="Hear this reply" onClick={()=>speak(m.text)}><Volume2 size={12}/>Listen</button>}</small>}{m.role==='assistant'&&m.route&&<Feedback api={api} agent={routeAgent(m.route)} surface="chat" source={m.source} label=""/>}</div>)}
      {busy&&<div className="chat-msg assistant typing">Fina is looking at your numbers…</div>}
      {!messages.length&&<div className="chat-suggestions">{suggestions.map(q=><button key={q} onClick={()=>ask(q)}>{q}</button>)}</div>}
    </>}<div ref={end}/></div>
    {profile&&<form className="chat-input" onSubmit={e=>{e.preventDefault();ask(text);}}><MicButton voice={voice}/><input aria-label="Your question" value={voice.listening?voice.interim:text} onChange={e=>setText(e.target.value)} maxLength="500" placeholder={voice.listening?'Listening…':voice.supported?'Ask Fina, or tap the mic…':'Ask Fina…'} disabled={busy||voice.listening}/><button className="primary" aria-label="Send" disabled={busy||!text.trim()}><Send size={15}/></button></form>}{voice.error&&<small className="error-text chat-voice-error">{voice.error}</small>}
  </section>}<button className="chat-launcher" aria-label={open?'Close chat':'Chat with Fina'} onClick={()=>setOpen(!open)}>{open?<X size={22}/>:<MessageCircle size={22}/>}</button></div>;
}

/**
 * A relative time such as "5 min ago".
 *
 * @param t timestamp
 * @returns Text.
 * Used in: Admin tables.
 */
const since = t => { if(!t) return ''; const d=new Date(t.endsWith?.('Z')||/[+-]\d\d:\d\d$/.test(t)?t:t+'Z'); const m=Math.round((Date.now()-d)/60000); return m<1?'just now':m<60?`${m} min ago`:m<1440?`${Math.round(m/60)} h ago`:d.toISOString().slice(0,10); };
/**
 * A short local date and time.
 *
 * @param t timestamp
 * @returns Text.
 * Used in: Admin tables.
 */
const clock = t => { if(!t) return ''; const d=new Date(/Z$|[+-]\d\d:\d\d$/.test(t)?t:t+'Z'); return d.toLocaleString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit'}); };
const actionNames = {register:'Signed up', sign_in:'Signed in', manual_entry:'Added a transaction', csv_preview:'Previewed a CSV', csv_import:'Imported a CSV', category_set:'Set a category', category_note:'Asked AI with a note', category_retry:'Retried AI categories', forecast:'Created a forecast', what_if:'Ran a what-if', fina_question:'Asked Fina', investigator:'Got Fina\'s questions', profile_update:'Updated their context', fina_message:'Messaged Fina', goal_create:'Added a goal', goal_update:'Updated a goal', goal_delete:'Removed a goal', goal_coach:'Asked Fina about a goal', advice:'Asked for options', goal_advice:'Asked for goal ideas', outlook:'Opened Outlook', outlook_explain:'Asked Fina about patterns', referral:'Joined with a referral code', points_award:'Earned FinaPoints', points_reversal:'FinaPoints reversed', redeem:'Redeemed a voucher', debt_add:'Added a debt',debt_update:'Updated a debt',voice_transcribe:'Used voice input', emergency_reroute:'Re-planned goals after an emergency', invest_explain:'Asked about investing', achievement:'Unlocked an achievement',fina_blocked:'Fina reply blocked by the coordinator',sharing_changed:'Changed data sharing',sharing_withdrawn:'Withdrew shared data',eval_run:'Started an evaluation',eval_cancel:'Cancelled an evaluation'};

/**
 * Admin overview: prompt release, AI status, policy controls and categorization outcomes.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Overview.
 */
function OpsOverview({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  async function load(){try{setData(await api('/ops/v1/overview'));}catch(e){setError(e.message);}}
  useEffect(()=>{load();},[]);
  async function policy(e){e.preventDefault();setBusy(true);try{const values=Object.fromEntries(new FormData(e.currentTarget));await api('/ops/v1/policy',{method:'PUT',body:JSON.stringify({...values,expected_revision:data.policy.revision})});await load();}catch(e){setError(e.message);}finally{setBusy(false);}}
  return <>{error&&<p className="error" role="alert">{error}</p>}{data&&<><div className="stats"><div className="stat"><span>Selected source release</span><strong>{data.prompt_release.release_version}</strong><small>Draft · evaluations not run</small></div><div className="stat"><span>AI inference</span><strong>{data.ai.live_inference_enabled?'Categorization enabled':'Disabled'}</strong><small>{data.ai.configured?'AI provider configured':'AI credentials not configured'}</small></div><div className="stat"><span>Recorded forecasts</span><strong>{data.metrics.length}</strong><small>Most recent 200 operations</small></div></div><section className="panel"><h2>Policy controls</h2><p>Current: <strong>{data.policy.mode}</strong> · revision {data.policy.revision}</p><form onSubmit={policy} className="flow-row"><select name="mode" aria-label="Policy mode" defaultValue={data.policy.mode}>{['AI_PAUSED','ADVICE_PAUSED','RUNNING'].map(m=><option key={m}>{m}</option>)}</select><input name="reason" aria-label="Reason for policy change" required minLength="5" maxLength="200" placeholder="Reason for change"/><button disabled={busy} className="primary">Update policy</button></form><p className="muted small-text">With Mesh configured, RUNNING and ADVICE_PAUSED enable transaction categorization. AI_PAUSED blocks inference. Advice is not active yet.</p></section><section className="panel"><h2>Categorization outcomes</h2><p className="muted">Counts since the API last started, across all customers. No transaction text or customer identity. Grafana keeps the history.</p><table><thead><tr><th>Source</th><th>Status</th><th>What it means</th><th className="right">Count</th></tr></thead><tbody>{data.categorizations.length?[...data.categorizations].sort((a,b)=>b.count-a.count).map(c=><tr key={c.source+c.status}><td>{c.source}</td><td>{c.status}</td><td className="muted">{opsMeaning[c.status]||''}</td><td className="right">{c.count}</td></tr>):<tr><td colSpan="4" className="muted">No categorizations since the API started.</td></tr>}</tbody></table>{data.categorizations.some(c=>['paused','not_configured'].includes(c.status))&&<p className="muted small-text">Rows that arrived while AI was off stay uncategorized until the customer retries or labels them. Set RUNNING above to let retries reach the model.</p>}</section><section className="panel"><h2>Prompt inventory</h2><table><thead><tr><th>Agent</th><th>Version</th><th>SHA-256</th></tr></thead><tbody>{Object.entries(data.prompt_release.templates).map(([role,t])=><tr key={role}><td>{role}</td><td>{t.version}</td><td><code title={t.sha256}>{t.sha256.slice(0,20)}…</code></td></tr>)}</tbody></table></section></>}</>;
}

/**
 * Admin activity by pseudonym, never usernames or content.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Users & activity.
 */
function OpsUsers({api}){
  const [users,setUsers]=useState(null),[events,setEvents]=useState([]),[actor,setActor]=useState(null),[error,setError]=useState('');
  async function load(){try{const [u,a]=await Promise.all([api('/ops/v1/users'),api(`/ops/v1/activity${actor?`?actor=${actor}`:''}`)]);setUsers(u.users);setEvents(a.events);}catch(e){setError(e.message);}}
  useEffect(()=>{load();const t=setInterval(load,10000);return ()=>clearInterval(t);},[actor]);
  return <>{error&&<p className="error" role="alert">{error}</p>}
    <section className="panel"><div className="section-title"><div><h2>Users</h2><p className="muted">Anonymous IDs only. Counts of what each person did, never what they entered.</p></div><span className="badge">{users?.length??0} ACTIVE</span></div>
    <div className="table-wrap"><table><thead><tr><th>User</th><th>Joined</th><th>Last active</th><th className="right">Transactions</th><th className="right">Forecasts</th><th className="right">What-ifs</th><th className="right">Fina</th><th className="right">AI calls</th><th></th></tr></thead><tbody>{(users||[]).map(u=><tr key={u.actor} className={actor===u.actor?'selected':''}><td><code>{u.actor}</code></td><td className="muted">{since(u.first_seen)}</td><td>{since(u.last_active)}</td><td className="right">{u.transactions_added}</td><td className="right">{u.actions.forecast||0}</td><td className="right">{u.actions.what_if||0}</td><td className="right">{u.actions.fina_question||0}</td><td className="right">{u.ai_calls}</td><td><button className="text-button" onClick={()=>setActor(actor===u.actor?null:u.actor)}>{actor===u.actor?'Show all':'Requests'}</button></td></tr>)}{users&&!users.length&&<tr><td colSpan="9" className="muted">No activity recorded yet.</td></tr>}</tbody></table></div></section>
    <section className="panel"><div className="section-title"><div><h2>Requests {actor&&<code>{actor}</code>}</h2><p className="muted">Newest first, refreshed every 10 seconds. Agent, model, outcome and time taken; no amounts, descriptions, notes or questions.</p></div></div>
    <div className="table-wrap"><table><thead><tr><th>Time</th><th>User</th><th>Request</th><th>Agent</th><th>Model</th><th>Outcome</th><th className="right">Items</th><th className="right">Took</th></tr></thead><tbody>{events.map((e,i)=><tr key={i}><td className="muted">{clock(e.time)}</td><td><code>{e.actor}</code></td><td>{actionNames[e.action]||e.action}</td><td>{e.agent||'—'}</td><td className="muted small-text">{e.model||'—'}</td><td>{e.outcome}</td><td className="right">{e.count??''}</td><td className="right">{e.duration_ms>=1000?`${(e.duration_ms/1000).toFixed(1)} s`:`${e.duration_ms} ms`}</td></tr>)}{!events.length&&<tr><td colSpan="8" className="muted">No requests yet.</td></tr>}</tbody></table></div></section></>;
}

/**
 * Admin request log and policy changes.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Logs.
 */
function OpsLogs({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[filter,setFilter]=useState('all');
  async function load(){try{setData(await api('/ops/v1/logs'));}catch(e){setError(e.message);}}
  useEffect(()=>{load();const t=setInterval(load,5000);return ()=>clearInterval(t);},[]);
  const rows=(data?.requests||[]).filter(r=>filter==='all'||(filter==='errors'?r.status>=400:r.route.startsWith(filter)));
  return <>{error&&<p className="error" role="alert">{error}</p>}
    <section className="panel"><div className="section-title"><div><h2>Request log</h2><p className="muted">Last 500 API requests since the server started, live every 5 seconds. Route templates and status only; request bodies are never logged.</p></div><select aria-label="Filter log" value={filter} onChange={e=>setFilter(e.target.value)} className="log-filter"><option value="all">All requests</option><option value="errors">Errors (4xx/5xx)</option><option value="/api/v1/assistant">Fina</option><option value="/api/v1/transactions">Transactions</option><option value="/ops">Operations</option></select></div>
    <div className="table-wrap log"><table><thead><tr><th>Time</th><th>Method</th><th>Route</th><th>Status</th><th className="right">Took</th><th>Error</th></tr></thead><tbody>{rows.map((r,i)=><tr key={i}><td className="muted">{clock(r.time)}</td><td>{r.method}</td><td><code>{r.route}</code></td><td><span className={`status s${String(r.status)[0]}`}>{r.status}</span></td><td className="right">{r.duration_ms} ms</td><td className="muted">{r.error||''}</td></tr>)}{!rows.length&&<tr><td colSpan="6" className="muted">Nothing logged yet.</td></tr>}</tbody></table></div></section>
    <section className="panel"><h2>Policy changes</h2><table><thead><tr><th>Time</th><th>Mode</th><th>Revision</th><th>Reason</th></tr></thead><tbody>{(data?.policy_changes||[]).map(a=><tr key={a.revision}><td className="muted">{clock(a.time)}</td><td>{a.mode}</td><td>{a.revision}</td><td>{a.reason}</td></tr>)}{data&&!data.policy_changes.length&&<tr><td colSpan="4" className="muted">No policy changes yet.</td></tr>}</tbody></table></section></>;
}

/**
 * Admin per-agent thumbs up/down and reasons.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Feedback.
 */
function OpsFeedback({api}){
  const [d,setD]=useState(null),[error,setError]=useState('');
  useEffect(()=>{const load=()=>api('/ops/v1/feedback').then(setD).catch(e=>setError(e.message));load();const t=setInterval(load,15000);return ()=>clearInterval(t);},[]);
  if(error)return <p className="error">{error}</p>;
  if(!d)return <div className="empty"><FinMark animated/>Loading feedback…</div>;
  const pct=(u,dn)=>u+dn?`${Math.round(u*100/(u+dn))}%`:'—';
  const reasonNames=Object.fromEntries(feedbackReasons);
  return <><section className="panel"><div className="section-title"><div><h2>How each agent is doing</h2><p className="muted">Customers' thumbs up and down on each step, lowest satisfaction first. Fixed reasons only; no text or customer data.</p></div><span className="badge">{d.total} VOTES</span></div>
    <div className="table-wrap"><table><thead><tr><th>Agent</th><th className="right">👍</th><th className="right">👎</th><th>Satisfaction</th><th className="right">AI answers</th><th className="right">Calculated</th><th>Top 👎 reasons</th></tr></thead><tbody>{d.agents.map(a=><tr key={a.agent}><td><strong>{agentNames[a.agent]||a.agent}</strong></td><td className="right">{a.up}</td><td className="right">{a.down}</td><td><div className="sat"><div className="sat-bar"><i style={{width:`${a.satisfaction??0}%`}} className={a.satisfaction<60?'low':a.satisfaction<80?'mid':''}/></div><span>{a.satisfaction!=null?`${a.satisfaction}%`:'—'}</span></div></td><td className="right">{a.by_source.ai?pct(a.by_source.ai.up,a.by_source.ai.down):'—'}</td><td className="right">{a.by_source.calculation?pct(a.by_source.calculation.up,a.by_source.calculation.down):'—'}</td><td className="muted small-text">{Object.entries(a.reasons).sort((x,y)=>y[1]-x[1]).slice(0,3).map(([k,n])=>`${reasonNames[k]||k} (${n})`).join(', ')||'—'}</td></tr>)}{!d.agents.length&&<tr><td colSpan="7" className="muted">No feedback yet.</td></tr>}</tbody></table></div></section>
    <section className="panel"><h2>Recent votes</h2><div className="table-wrap log"><table><thead><tr><th>Time</th><th>User</th><th>Agent</th><th>Where</th><th>Answer</th><th>Vote</th><th>Reason</th></tr></thead><tbody>{d.recent.map((v,i)=><tr key={i}><td className="muted">{clock(v.time)}</td><td><code>{v.actor}</code></td><td>{agentNames[v.agent]||v.agent}</td><td className="muted">{v.surface.replaceAll('_',' ')}</td><td className="muted">{v.source||'—'}</td><td>{v.rating>0?'👍':'👎'}</td><td className="muted">{v.reason?reasonNames[v.reason]:''}</td></tr>)}</tbody></table></div></section></>;
}

/**
 * Embedded Grafana dashboard when reachable.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Grafana.
 */
function OpsGrafana({api}){
  const [g,setG]=useState(null);
  useEffect(()=>{api('/ops/v1/grafana').then(setG).catch(()=>setG({reachable:false}));},[]);
  if(!g)return <div className="empty"><FinMark animated/>Checking Grafana…</div>;
  return <section className="panel"><div className="section-title"><div><h2>Grafana · FinPilot overview</h2><p className="muted">Live Prometheus metrics: traffic, latency, agent nodes, model calls, tokens and categorization.</p></div>{g.url&&<a className="secondary link-button" href={g.dashboard} target="_blank" rel="noreferrer">Open in Grafana<ArrowUpRight size={14}/></a>}</div>
    {g.reachable?<><p className="muted small-text">If the frame asks you to sign in, use the Grafana admin login from your <code>.env</code> once.</p><iframe className="grafana-frame" title="Grafana dashboard" src={g.dashboard}/></>:<div className="empty"><BarChart3 size={34}/><h3>Grafana isn't running.</h3><p>Start Docker Desktop, set <code>GRAFANA_ADMIN_PASSWORD</code> in <code>.env</code>, then run <code>docker compose -f compose.observability.yaml up -d</code> and reopen this tab.</p></div>}</section>;
}

/**
 * Format a small dollar cost with enough decimals.
 *
 * @param v USD
 * @returns Text.
 * Used in: Admin > LangSmith.
 */
const usd = v => v==null?'—':v===0?'$0':v<0.01?`$${v.toFixed(5)}`:`$${v.toFixed(4)}`;

/**
 * Recent sanitized LangSmith runs and costs.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > LangSmith.
 */
function OpsLangSmith({api}){
  const [d,setD]=useState(null);
  async function load(){try{setD(await api('/ops/v1/langsmith'));}catch(e){setD({error:e.message});}}
  useEffect(()=>{load();const t=setInterval(load,30000);return ()=>clearInterval(t);},[]);
  if(!d)return <div className="empty"><FinMark animated/>Loading LangSmith runs…</div>;
  return <section className="panel"><div className="section-title"><div><h2>LangSmith · {d.project}</h2><p className="muted">Recent traced agent runs. Traces are sanitized before export: agent, model, prompt version, outcome and tokens only. LangSmith can't be shown in a frame, so runs are listed here.</p></div>{d.url&&<a className="secondary link-button" href={d.url} target="_blank" rel="noreferrer">Open in LangSmith<ArrowUpRight size={14}/></a>}</div>
    {d.enabled===false?<div className="empty"><Activity size={34}/><h3>Tracing is off.</h3><p>Set <code>LANGSMITH_TRACING=true</code> and <code>LANGSMITH_API_KEY</code> in <code>.env</code>, then restart the API.</p></div>:d.error?<p className="error">{d.error}</p>:
    <>{d.costs&&<div className="stats cost-stats"><div className="stat"><span>Model calls shown</span><strong>{d.costs.model_runs}</strong><small>Latest 50 runs</small></div><div className="stat"><span>LangSmith cost</span><strong>{usd(d.costs.langsmith_total)}</strong><small>{d.costs.langsmith_priced} of {d.costs.model_runs} calls priced by LangSmith</small></div><div className="stat highlight"><span>At your prices</span><strong>{usd(d.costs.your_total)}</strong><small>{d.costs.prices_configured.length?`${d.costs.your_priced} of ${d.costs.model_runs} calls priced from MODEL_PRICES`:'Set MODEL_PRICES in .env to price every model'}</small></div></div>}
    <p className="muted small-text">LangSmith uses its own price list, which can differ from what your provider charges and has no price for some models. "Your price" uses the per-million-token prices in MODEL_PRICES.</p>
    <div className="table-wrap"><table><thead><tr><th>Time</th><th>Run</th><th>Type</th><th>Model</th><th>Outcome</th><th>Status</th><th className="right">Tokens</th><th className="right">LangSmith cost</th><th className="right">Your price</th><th className="right">Latency</th></tr></thead><tbody>{d.runs.map((r,i)=><tr key={i}><td className="muted">{clock(r.time)}</td><td>{r.name}</td><td className="muted">{r.type}</td><td className="muted small-text">{r.model||'—'}</td><td>{r.outcome||'—'}</td><td><span className={`status ${r.status==='success'?'s2':r.status==='error'?'s5':'s3'}`}>{r.status}</span></td><td className="right">{r.tokens??''}</td><td className="right">{r.type==='llm'?usd(r.langsmith_cost):''}</td><td className="right">{r.type==='llm'?usd(r.your_cost):''}</td><td className="right">{r.latency_s!=null?`${r.latency_s} s`:''}</td></tr>)}{!d.runs.length&&<tr><td colSpan="10" className="muted">No runs yet.</td></tr>}</tbody></table></div></>}</section>;
}

/**
 * Seconds as "3 h 12 min".
 *
 * @param s seconds
 * @returns Text.
 * Used in: Admin health tiles.
 */
const uptime=s=>s<3600?`${Math.floor(s/60)} min`:s<86400?`${Math.floor(s/3600)} h ${Math.floor(s%3600/60)} min`:`${Math.floor(s/86400)} d ${Math.floor(s%86400/3600)} h`;
const usd2=v=>v==null?'—':v<0.01&&v>0?`$${v.toFixed(4)}`:`$${v.toFixed(2)}`;
const severityIcon={critical:TriangleAlert,warning:TriangleAlert,info:Activity};
const auditNames={policy_change:'Changed AI policy',ai_auto_pause:'Paused AI (budget reached)',budget_change:'Changed AI budget',suspend:'Suspended an account',unsuspend:'Unsuspended an account',alert_acknowledge:'Acknowledged an alert',alert_resolve:'Resolved an alert',alert_test:'Sent a test alert',eval_run:'Started an evaluation',eval_cancel:'Cancelled an evaluation',contribution_reject:'Removed a shared example',contribution_export:'Exported shared examples',backtest_run:'Started a forecast backtest'};
const limitNames={failed_sign_in:'Failed sign-in',ai_categorize_daily:'AI categorization (20/day)',chat_hourly:'Chat (20/hour)',voice_hourly:'Voice (40/hour)'};

/**
 * Admin health tiles, Telegram status and test button, and the alerts list.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Alerts & health.
 */
function OpsHealth({api}){
  const [health,setHealth]=useState(null),[alerts,setAlerts]=useState(null),[all,setAll]=useState(false),[error,setError]=useState(''),[note,setNote]=useState(''),[busy,setBusy]=useState('');
  async function load(){try{const [h,a]=await Promise.all([api('/ops/v1/health'),api(`/ops/v1/alerts?status=${all?'all':'active'}`)]);setHealth(h);setAlerts(a.alerts);setError('');}catch(e){setError(e.message);}}
  useEffect(()=>{load();const timer=setInterval(load,30000);return()=>clearInterval(timer);},[all]);
  async function act(id,action){setBusy(id);try{await api(`/ops/v1/alerts/${id}/${action}`,{method:'POST'});await load();}catch(e){setError(e.message);}finally{setBusy('');}}
  async function test(){setBusy('test');setNote('');try{await api('/ops/v1/alerts/test',{method:'POST'});setNote('Test alert sent. Check Telegram on your phone.');}catch(e){setError(e.message);}finally{setBusy('');}}
  if(!health)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Checking the system…</div>;
  const budgetPct=health.budget_usd?Math.round(health.spend_today_usd*100/health.budget_usd):null;
  const tile=(ok,label,value,sub)=><div className={`health-tile ${ok===false?'bad':ok==='warn'?'warn':'good'}`}><span>{ok===false?<TriangleAlert size={14}/>:ok==='warn'?<TriangleAlert size={14}/>:<Check size={14}/>}{label}</span><strong>{value}</strong><small>{sub}</small></div>;
  return <>{error&&<p className="error" role="alert">{error}</p>}
    <div className="health-grid">
      {tile(true,'API','Up',`for ${uptime(health.uptime_s)}`)}
      {tile(health.database.ok,'Database',health.database.ok?'Healthy':'Unreachable',`${health.database.latency_ms} ms${health.database.size_mb!=null?` · ${health.database.size_mb} MB`:''}`)}
      {tile(health.ai.mode==='AI_PAUSED'?'warn':true,'AI mode',{RUNNING:'Running',ADVICE_PAUSED:'Advice paused',AI_PAUSED:'Paused'}[health.ai.mode]||health.ai.mode,health.ai.last_success?`last success ${since(health.ai.last_success)}`:'no successful call since start')}
      {tile(health.errors_hour?false:true,'Errors (last hour)',`${health.errors_hour} of ${health.requests_hour}`,'requests failed with 5xx')}
      {tile(health.p95_ms>2000?'warn':true,'Response time',health.p95_ms!=null?`${Math.round(health.p95_ms)} ms`:'—','95th percentile, last hour')}
      {tile(health.model_today.failure_rate>10?false:health.model_today.failure_rate>2?'warn':true,'Model calls today',health.model_today.calls,health.model_today.failure_rate!=null?`${health.model_today.failure_rate}% failed or incomplete`:'none yet')}
      {tile(budgetPct>=100?false:budgetPct>=80?'warn':true,'AI spend today',usd2(health.spend_today_usd),health.budget_usd?`${budgetPct}% of ${usd2(health.budget_usd)} cap`:'no daily cap set')}
      {tile(health.alerts_open?'warn':true,'Open alerts',health.alerts_open,health.alerts_open?'need a look':'all clear')}
    </div>
    <section className="panel"><div className="section-title"><div><h2>Phone alerts</h2><p className="muted">{health.telegram?'Telegram is set up. Server errors, failing AI calls, sign-in spikes, possible abuse and budget alerts reach your phone, at most once every 15 minutes per alert.':'Telegram is not set up yet, so alerts are only listed here. Add TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to .env and restart the API.'}</p></div><button className="secondary" disabled={!health.telegram||busy==='test'} onClick={test}><Send size={14}/>{busy==='test'?'Sending…':'Send test alert'}</button></div>{note&&<p className="success-text">{note}</p>}</section>
    <section className="panel"><div className="section-title"><div><h2>Alerts</h2><p className="muted">What broke, where and how often. Never customer data.</p></div><div className="segmented" role="group" aria-label="Which alerts"><button className={!all?'active':''} aria-pressed={!all} onClick={()=>setAll(false)}>Active</button><button className={all?'active':''} aria-pressed={all} onClick={()=>setAll(true)}>All</button></div></div>
      {!alerts?.length?<div className="empty"><Check size={28}/><h3>No {all?'':'active '}alerts</h3><p>Everything is running quietly.</p></div>:<div className="alert-list">{alerts.map(a=>{const Icon=severityIcon[a.severity]||Activity;return <div key={a.id} className={`alert-row ${a.severity} ${a.status}`}>
        <span className={`sev ${a.severity}`}><Icon size={14}/>{a.severity}</span>
        <div className="alert-main"><strong>{a.title}{a.count>1&&<span className="badge">×{a.count}</span>}</strong><p>{a.detail}</p><small className="muted">First {clock(a.first)} · last {clock(a.last)} · {a.delivered?'sent to phone':'not sent to phone'} · {a.status}</small></div>
        <div className="alert-actions">{a.status==='open'&&<button className="secondary" disabled={busy===a.id} onClick={()=>act(a.id,'acknowledge')}>Acknowledge</button>}{a.status!=='resolved'&&<button className="primary" disabled={busy===a.id} onClick={()=>act(a.id,'resolve')}>Resolve</button>}</div>
      </div>;})}</div>}
    </section></>;
}

/**
 * Admin AI costs: today, month, daily cap form, 30-day chart and spend by model.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > AI costs.
 */
function OpsCosts({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[saved,setSaved]=useState(''),[hover,setHover]=useState(null);
  async function load(){try{setData(await api('/ops/v1/costs'));}catch(e){setError(e.message);}}
  useEffect(()=>{load();},[]);
  async function save(e){e.preventDefault();const f=new FormData(e.currentTarget);const cap=String(f.get('cap')).trim();setSaved('');setError('');
    try{await api('/ops/v1/budget',{method:'PUT',body:JSON.stringify({daily_budget_usd:cap===''?null:Number(cap),auto_pause:f.get('auto')==='on'})});setSaved('Budget saved.');await load();}catch(e){setError(e.message);}}
  if(!data)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Loading costs…</div>;
  const max=Math.max(...data.days.map(d=>d.cost_usd),data.budget.daily_usd||0,1e-9)*1.12;   // headroom so the cap line stays visible
  const byDay=Object.fromEntries(data.days.map(d=>[d.day,d]));
  const month=Array.from({length:30},(_,i)=>{const day=iso(new Date(Date.now()-(29-i)*864e5));return byDay[day]||{day,calls:0,failures:0,cost_usd:0};});
  return <>{error&&<p className="error" role="alert">{error}</p>}
    <div className="stats"><div className="stat"><span><Wallet size={17}/>Today</span><strong>{usd2(data.today_usd)}</strong><small>{data.budget.daily_usd?`${Math.round(data.today_usd*100/data.budget.daily_usd)}% of the daily cap`:'No daily cap'}</small></div><div className="stat"><span><BarChart3 size={17}/>This month</span><strong>{usd2(data.month_usd)}</strong><small>From configured model prices</small></div><div className="stat"><span><ShieldCheck size={17}/>Daily cap</span><strong>{data.budget.daily_usd?usd2(data.budget.daily_usd):'None'}</strong><small>{data.budget.auto_pause?'AI pauses itself at 100%':'Alerts only, no auto-pause'}</small></div></div>
    <section className="panel"><h2>Daily AI budget</h2><p className="muted">You get a phone alert at 80% and at 100%. With auto-pause on, AI is paused at 100% (set it back to RUNNING on Overview), and the pause is in the audit log.</p>
      <form className="budget-form" onSubmit={save}><label>Cap per day (USD)<input name="cap" type="number" min="0.01" step="0.01" defaultValue={data.budget.daily_usd??''} placeholder="No cap"/></label><label className="check-label"><input name="auto" type="checkbox" defaultChecked={data.budget.auto_pause}/>Pause AI automatically at the cap</label><button className="primary">Save budget</button></form>{saved&&<p className="success-text">{saved}</p>}</section>
    <section className="panel"><h2>Spend, last 30 days</h2><p className="muted">Each column is one day (UTC). Hover for calls and failures.{data.budget.daily_usd?' The dashed line is the daily cap.':''}</p>
      {!data.days.length?<div className="empty"><BarChart3 size={28}/><p>No model calls recorded yet.</p></div>:<div className="spend-chart" role="img" aria-label="AI spend per day">
        {data.budget.daily_usd&&<i className="cap-line" style={{bottom:`${22+data.budget.daily_usd/max*178}px`}}><span>cap {usd2(data.budget.daily_usd)}</span></i>}
        {month.map(d=><div key={d.day} className="spend-col" tabIndex={0} onMouseEnter={()=>setHover(d.day)} onMouseLeave={()=>setHover(null)} onFocus={()=>setHover(d.day)} onBlur={()=>setHover(null)}><i style={{height:d.cost_usd?`${Math.max(d.cost_usd/max*100,1)}%`:0}}/><span>{d.day.slice(5)}</span>{hover===d.day&&<span className="cat-tip" role="tooltip"><strong>{d.day}</strong>{usd2(d.cost_usd)} · {d.calls} call{d.calls===1?'':'s'}{d.failures?` · ${d.failures} failed`:''}</span>}</div>)}
      </div>}
      {data.models.length>0&&<table className="model-table"><thead><tr><th>Model</th><th className="right">Calls</th><th className="right">Cost</th></tr></thead><tbody>{data.models.map(m=><tr key={m.model}><td>{m.model}{!m.priced&&<small className="muted"> · no price set, counted as $0</small>}</td><td className="right">{m.calls}</td><td className="right">{usd2(m.cost_usd)}</td></tr>)}</tbody></table>}
    </section></>;
}

/**
 * Admin abuse and limits view with suspend and unsuspend.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Abuse & limits.
 */
function OpsAbuse({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[suspending,setSuspending]=useState(null),[busy,setBusy]=useState('');
  async function load(){try{setData(await api('/ops/v1/abuse'));}catch(e){setError(e.message);}}
  useEffect(()=>{load();},[]);
  async function suspend(e,actor){e.preventDefault();setBusy(actor);try{await api('/ops/v1/suspensions',{method:'POST',body:JSON.stringify({actor,reason:new FormData(e.currentTarget).get('reason')})});setSuspending(null);await load();}catch(e){setError(e.message);}finally{setBusy('');}}
  async function lift(actor){setBusy(actor);try{await api(`/ops/v1/suspensions/${actor}`,{method:'DELETE'});await load();}catch(e){setError(e.message);}finally{setBusy('');}}
  if(!data)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Loading…</div>;
  return <section className="panel"><div className="section-title"><div><h2>Abuse and limits</h2><p className="muted">Accounts that hit a usage limit or failed to sign in during the last {data.window_days} days, by pseudonym. Suspending blocks sign-in and every customer request straight away.</p></div></div>{error&&<p className="error" role="alert">{error}</p>}
    {!data.accounts.length?<div className="empty"><ShieldCheck size={28}/><h3>Nothing unusual</h3><p>No account has hit a limit this week.</p></div>:<div className="table-wrap"><table><thead><tr><th>Account</th><th>Limits hit</th><th className="right">Total</th><th>Last</th><th>Status</th><th></th></tr></thead><tbody>
      {data.accounts.map(a=><tr key={a.actor} className={a.suspended?'suspended-row':''}><td><code>{a.actor}</code></td><td><div className="limit-chips">{Object.entries(a.limits).map(([k,v])=><span key={k} className="limit-chip">{limitNames[k]||k} ×{v}</span>)}</div></td><td className="right">{a.total}</td><td className="muted">{a.last?since(a.last):'—'}</td>
        <td>{a.suspended?<span className="goal-pill bad" title={`By ${a.suspended_by}: ${a.suspended_reason}`}>Suspended</span>:<span className="goal-pill good">Active</span>}</td>
        <td className="right nowrap">{a.suspended?<button className="secondary small-button" disabled={busy===a.actor} onClick={()=>lift(a.actor)}>Unsuspend</button>:suspending===a.actor?<form className="suspend-form" onSubmit={e=>suspend(e,a.actor)}><input name="reason" required minLength="5" maxLength="200" placeholder="Reason (kept in the audit log)" autoFocus/><button className="primary small-button" disabled={busy===a.actor}>Suspend</button><button type="button" className="text-button" onClick={()=>setSuspending(null)}>Cancel</button></form>:<button className="secondary small-button" onClick={()=>setSuspending(a.actor)}>Suspend…</button>}</td></tr>)}
    </tbody></table></div>}</section>;
}

/**
 * Admin audit log of operator and system actions.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Audit log.
 */
function OpsAudit({api}){
  const [data,setData]=useState(null),[error,setError]=useState('');
  useEffect(()=>{api('/ops/v1/audit').then(setData).catch(e=>setError(e.message));},[]);
  if(!data)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Loading…</div>;
  return <section className="panel"><h2>Audit log</h2><p className="muted">Every operator action, and automatic ones by the system, newest first.</p>
    {!data.entries.length?<div className="empty"><List size={28}/><p>No admin actions yet.</p></div>:<div className="table-wrap"><table><thead><tr><th>When</th><th>Who</th><th>Action</th><th>On</th><th>Details</th></tr></thead><tbody>{data.entries.map((e,i)=><tr key={i}><td className="muted nowrap">{clock(e.time)}</td><td>{e.operator==='system'?<span className="goal-pill muted">system</span>:e.operator}</td><td>{auditNames[e.action]||e.action}</td><td>{e.target?<code>{e.target}</code>:'—'}</td><td className="muted">{e.detail||'—'}</td></tr>)}</tbody></table></div>}</section>;
}

const suiteNames={coordinator:'Coordinator',categorizer:'Categorizer',forecaster:"Forecaster (Fina's answers)",goals_coach:'Goals coach',risk:'Risk reviewer',system:'End to end'};
/**
 * Start, follow and cancel an evaluation run from the admin portal.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.onFinished called with the new run id when a run finishes, to show its results
 * @returns Panel element.
 * Used in: Admin > Evaluations (POST /ops/v1/evals/run, GET /ops/v1/evals/status, POST /ops/v1/evals/cancel).
 */
function EvalRunner({api,onFinished}){
  const [job,setJob]=useState(null),[picked,setPicked]=useState(Object.keys(suiteNames)),[split,setSplit]=useState('dev'),[judge,setJudge]=useState(true),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const active=job&&['running','cancelling'].includes(job.status);
  useEffect(()=>{let stop=false,last=null;
    const poll=()=>api('/ops/v1/evals/status').then(j=>{if(stop)return;setJob(j);if(last&&['running','cancelling'].includes(last)&&j.status==='finished')onFinished(j.run_id);last=j.status;}).catch(()=>{});
    poll();const timer=setInterval(poll,4000);return()=>{stop=true;clearInterval(timer);};},[]);
  async function start(){setBusy(true);setError('');try{setJob(await api('/ops/v1/evals/run',{method:'POST',body:JSON.stringify({suites:picked,split,judge})}));}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function cancel(){try{setJob(await api('/ops/v1/evals/cancel',{method:'POST'}));}catch(e){setError(e.message);}}
  const toggle=k=>setPicked(p=>p.includes(k)?p.filter(x=>x!==k):[...p,k]);
  const total=job?.suites?.length||0,done=job?.done?.length||0;
  return <section className="panel eval-runner"><div className="section-title"><div><h2>Run an evaluation</h2><p className="muted">Runs the chosen suites against live models on a throwaway database (never customer data), one agent at a time. Uses a small amount of model spend; with the judge on, about twice the calls.</p></div></div>
    {active?<div className="eval-progress"><div className="sat-bar wide"><i style={{width:`${total?Math.max(done/total*100,4):4}%`}}/></div>
      <p><strong>{job.status==='cancelling'?'Cancelling…':`Running ${suiteNames[job.current]||'…'}`}</strong> · {done} of {total} suites done · run {job.run_id} · started by {job.by}</p>
      {job.log_tail?.length>0&&<pre className="eval-log">{job.log_tail.join('\n')}</pre>}
      <button className="secondary" disabled={job.status==='cancelling'} onClick={cancel}><X size={14}/>Cancel</button></div>
    :<><div className="eval-options"><fieldset><legend>Agents</legend>{Object.entries(suiteNames).map(([k,v])=><label key={k} className="check-label"><input type="checkbox" checked={picked.includes(k)} onChange={()=>toggle(k)}/>{v}</label>)}</fieldset>
      <div className="eval-side"><label>Golden cases<select value={split} onChange={e=>setSplit(e.target.value)}><option value="dev">Development set</option><option value="holdout">Holdout set</option><option value="all">All cases</option></select></label>
        <label className="check-label"><input type="checkbox" checked={judge} onChange={e=>setJudge(e.target.checked)}/>Score with the AI judge (faithfulness, relevance, tone)</label>
        <button className="primary" disabled={busy||!picked.length} onClick={start}><Activity size={15}/>{busy?'Starting…':'Run evaluation'}</button></div></div>
      {job&&['finished','cancelled'].includes(job.status)&&<p className="muted small-text">Last run {job.run_id}: {job.status} at {job.finished_at?.replace('T',' ')} · {job.done?.map(d=>`${suiteNames[d.suite]||d.suite} ${d.exit_code===0?'all passed':'had failures'}`).join(' · ')}</p>}</>}
    {error&&<p className="error" role="alert">{error}</p>}
  </section>;
}

/**
 * Admin review of examples customers chose to share: counts, the examples, removal and export as draft goldens.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Panel element.
 * Used in: Admin > Evaluations (GET/DELETE /ops/v1/contributions, GET /ops/v1/contributions/export).
 */
function Contributions({api}){
  const [data,setData]=useState(null),[error,setError]=useState(''),[kind,setKind]=useState('categorizer');
  const load=()=>api('/ops/v1/contributions').then(setData).catch(e=>setError(e.message));
  useEffect(()=>{load();},[]);
  async function reject(id){try{await api(`/ops/v1/contributions/${id}`,{method:'DELETE'});await load();}catch(e){setError(e.message);}}
  async function exportDraft(){try{const draft=await api(`/ops/v1/contributions/export?kind=${kind}`);const url=URL.createObjectURL(new Blob([JSON.stringify(draft,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=`${draft.dataset_version}.json`;a.click();URL.revokeObjectURL(url);}catch(e){setError(e.message);}}
  if(!data)return null;
  const items=data.items.filter(i=>i.kind===kind);
  return <section className="panel"><div className="section-title"><div><h2>Contributed examples</h2><p className="muted">De-identified examples from customers who opted in on Preferences, by pseudonym. Remove anything unsuitable, then export a draft golden file to review before adding it to evals/goldens/.</p></div>
    <div className="button-row tight"><div className="segmented" role="group" aria-label="Kind"><button className={kind==='categorizer'?'active':''} aria-pressed={kind==='categorizer'} onClick={()=>setKind('categorizer')}>Categories ({data.counts.categorizer||0})</button><button className={kind==='coordinator'?'active':''} aria-pressed={kind==='coordinator'} onClick={()=>setKind('coordinator')}>Questions ({data.counts.coordinator||0})</button></div>
      <button className="secondary" disabled={!items.length} onClick={exportDraft}><FileUp size={14}/>Export as draft goldens</button></div></div>
    {error&&<p className="error" role="alert">{error}</p>}
    {!items.length?<p className="muted">Nothing shared yet.</p>:<div className="table-wrap"><table><thead><tr>{kind==='categorizer'?<><th>Merchant line</th><th>Type</th><th className="right">Amount</th><th>Category</th></>:<><th>Question</th><th>Page</th><th>Route</th><th>Card</th></>}<th>From</th><th></th></tr></thead><tbody>
      {items.map(i=><tr key={i.id}>{kind==='categorizer'?<><td>{i.payload.description}</td><td className="muted">{i.payload.kind}</td><td className="right">{money(i.payload.amount_minor)}</td><td><span className="category">{i.payload.category}</span></td></>
        :<><td>{i.payload.question}</td><td className="muted">{i.payload.page||'chat'}</td><td><code>{i.payload.route}</code></td><td className="muted small-text">{Object.entries(i.payload.card||{}).map(([k,v])=>`${k}: ${v}`).join(' · ')||'—'}</td></>}
        <td><code>{i.actor}</code></td><td className="right"><button className="icon-button" aria-label="Remove this example" onClick={()=>reject(i.id)}><Trash2 size={14}/></button></td></tr>)}
    </tbody></table></div>}
  </section>;
}

/**
 * Admin Evaluations: DeepEval results per agent with pass rates, judge scores, the trend across runs, failures and
 * every case.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Evaluations (data from GET /ops/v1/evals, written by evals/deepeval runs).
 */
function OpsEvals({api}){
  const [run,setRun]=useState(''),[data,setData]=useState(null),[error,setError]=useState(''),[suite,setSuite]=useState('all'),[onlyFailed,setOnlyFailed]=useState(false),[open,setOpen]=useState(null),[hover,setHover]=useState(null);
  useEffect(()=>{setData(null);api(`/ops/v1/evals${run?`?run=${encodeURIComponent(run)}`:''}`).then(setData).catch(e=>setError(e.message));},[run]);
  if(!data)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Loading evaluations…</div>;
  const runner=<EvalRunner api={api} onFinished={id=>{setRun(id);api(`/ops/v1/evals?run=${encodeURIComponent(id)}`).then(setData).catch(()=>{});}}/>;
  if(!data.runs.length)return <>{runner}<Contributions api={api}/><section className="panel"><div className="empty"><BarChart3 size={28}/><h3>No evaluation runs yet</h3><p>Start one above; each run appears here with its charts.</p></div></section></>;
  const {meta,summary,cases}=data,name=s=>suiteNames[s]||s;
  const code=summary.metrics.filter(m=>!m.judge),judge=summary.metrics.filter(m=>m.judge&&m.avg_score!=null);
  const codeCount=code.reduce((a,m)=>a+m.count,0),codePass=codeCount?Math.round(code.reduce((a,m)=>a+m.passed,0)*1000/codeCount)/10:null;
  const judgeAvg=judge.length?Math.round(judge.reduce((a,m)=>a+m.avg_score,0)/judge.length*100)/100:null;
  const failures=cases.filter(c=>!c.passed),shown=cases.filter(c=>(suite==='all'||c.suite===suite)&&(!onlyFailed||!c.passed));
  const trend=[...data.runs].reverse();
  const tw=520,th=150,tx=i=>trend.length>1?24+i*(tw-48)/(trend.length-1):tw/2,ty=v=>th-20-(v??0)/100*(th-40);
  return <>
    {runner}
    <section className="panel evals-head"><div className="section-title"><div><h2>Agent evaluations</h2><p className="muted">DeepEval runs on synthetic goldens through the real API. Code checks are the gate; judge scores come from a different model than the one tested.</p></div>
      <label className="inline-label">Run<select value={run||meta.run_id} onChange={e=>setRun(e.target.value)}>{data.runs.map(r=><option key={r.run_id} value={r.run_id}>{r.run_id} · {r.pass_rate}%</option>)}</select></label></div>
      <p className="muted small-text">Finished {meta.finished_at?.replace('T',' ')||'—'} · prompt release {meta.prompt_release||'—'} · agents {meta.agent_model||'—'} · judge {meta.judge?meta.judge_model:'off'} · split {meta.split||'—'}{meta.git_commit?` · commit ${meta.git_commit}`:''}</p></section>
    <div className="health-grid">
      <div className={`health-tile ${summary.pass_rate>=95?'good':summary.pass_rate>=80?'warn':'bad'}`}><span><Check size={14}/>Cases passed</span><strong>{summary.pass_rate}%</strong><small>{summary.passed} of {summary.total} cases</small></div>
      <div className={`health-tile ${codePass>=98?'good':codePass>=90?'warn':'bad'}`}><span><ShieldCheck size={14}/>Code checks</span><strong>{codePass??'—'}%</strong><small>{codeCount} checks · the release gate</small></div>
      <div className={`health-tile ${judgeAvg==null?'good':judgeAvg>=0.8?'good':judgeAvg>=0.6?'warn':'bad'}`}><span><Sparkles size={14}/>Judge average</span><strong>{judgeAvg??'—'}</strong><small>{judge.length?`${judge.length} DeepEval metrics, 0 to 1`:'judge was off for this run'}</small></div>
      <div className={`health-tile ${failures.length?'warn':'good'}`}><span><TriangleAlert size={14}/>Failing cases</span><strong>{failures.length}</strong><small>{failures.length?'listed below':'all clear'}</small></div>
    </div>
    <div className="evals-grid">
      <section className="panel"><h2>Pass rate by agent</h2><p className="muted">Share of cases where every check passed. Click an agent to filter the cases below.</p>
        <ul className="cat-bars">{summary.suites.map(s=><li key={s.suite} tabIndex={0} role="button" className={suite===s.suite?'selected':''} onClick={()=>setSuite(suite===s.suite?'all':s.suite)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();setSuite(suite===s.suite?'all':s.suite);}}} onMouseEnter={()=>setHover(s.suite)} onMouseLeave={()=>setHover(null)} aria-label={`${name(s.suite)}: ${s.passed} of ${s.cases} passed`}>
          <span className="cat-name">{name(s.suite)}</span><span className="cat-track"><i style={{width:`${Math.max(s.pass_rate,1)}%`}}/></span><span className="cat-value">{s.passed}/{s.cases}</span>
          {hover===s.suite&&<span className="cat-tip" role="tooltip"><strong>{name(s.suite)}</strong>{s.pass_rate}% passed · {s.cases-s.passed} failing</span>}</li>)}</ul></section>
      <section className="panel"><h2>Judge scores</h2><p className="muted">Average DeepEval score per metric (0 to 1). The mark on each bar is its pass threshold.</p>
        {!judge.length?<div className="empty"><Sparkles size={24}/><p>No judge metrics in this run.</p></div>:<ul className="cat-bars judge-bars">{judge.map(m=><li key={m.metric} tabIndex={0} onMouseEnter={()=>setHover(m.metric)} onMouseLeave={()=>setHover(null)} aria-label={`${m.metric}: average ${m.avg_score}, threshold ${m.threshold}`}>
          <span className="cat-name">{m.metric.replace(' [GEval]','')}</span><span className="cat-track"><i style={{width:`${Math.max(m.avg_score*100,1)}%`}}/><b className="threshold" style={{left:`${m.threshold*100}%`}}/></span><span className="cat-value">{m.avg_score.toFixed(2)}</span>
          {hover===m.metric&&<span className="cat-tip" role="tooltip"><strong>{m.metric}</strong>average {m.avg_score} · threshold {m.threshold}<br/>{m.passed} of {m.count} passed</span>}</li>)}</ul>}</section>
    </div>
    <section className="panel"><h2>Pass rate over runs</h2><p className="muted">{trend.length<2?'One run so far. Each run you start above adds a point.':`${trend.length} runs, oldest on the left. Hover a point for its run.`}</p>{
      <svg className="trend" viewBox={`0 0 ${tw} ${th}`} role="img" aria-label="Pass rate per run">{[0,50,100].map(v=><g key={v}><line x1="24" x2={tw-24} y1={ty(v)} y2={ty(v)} className="grid"/><text x="0" y={ty(v)+4} className="chart-label">{v}%</text></g>)}
        <polyline points={trend.map((r,i)=>`${tx(i)},${ty(r.pass_rate)}`).join(' ')} className="trend-line"/>
        {trend.map((r,i)=><g key={r.run_id} onMouseEnter={()=>setHover(r.run_id)} onMouseLeave={()=>setHover(null)}><circle cx={tx(i)} cy={ty(r.pass_rate)} r="5" className="trend-dot"/><circle cx={tx(i)} cy={ty(r.pass_rate)} r="14" fill="transparent"/>{hover===r.run_id&&<text x={tx(i)} y={ty(r.pass_rate)-12} textAnchor="middle" className="chart-label strong">{r.pass_rate}% · {r.run_id}</text>}</g>)}</svg>}</section>
    {failures.length>0&&<section className="panel"><h2>Failures</h2><div className="alert-list">{failures.map(c=><div key={c.suite+c.case} className="alert-row warning"><span className="sev warning"><TriangleAlert size={14}/>{name(c.suite)}</span><div className="alert-main"><strong>{c.case}</strong>{c.metrics.filter(m=>!m.passed).map(m=><p key={m.metric}><b>{m.metric}</b>{m.score!=null?` (${Math.round(m.score*100)/100})`:''}: {m.reason}</p>)}</div></div>)}</div></section>}
    <Contributions api={api}/>
    <section className="panel"><div className="section-title"><div><h2>All cases</h2><p className="muted">{shown.length} of {cases.length} shown. Click a case to compare its expected result with the agent's output.</p></div>
      <div className="button-row tight"><select aria-label="Agent" value={suite} onChange={e=>setSuite(e.target.value)}><option value="all">All agents</option>{summary.suites.map(s=><option key={s.suite} value={s.suite}>{name(s.suite)}</option>)}</select><label className="check-label"><input type="checkbox" checked={onlyFailed} onChange={e=>setOnlyFailed(e.target.checked)}/>Only failing</label></div></div>
      <div className="table-wrap"><table><thead><tr><th>Case</th><th>Agent</th><th>Split</th><th>Result</th><th>Metrics</th></tr></thead><tbody>{shown.map(c=><React.Fragment key={c.suite+c.case}><tr className="case-row" onClick={()=>setOpen(open===c.suite+c.case?null:c.suite+c.case)}><td><code>{c.case}</code></td><td>{name(c.suite)}</td><td className="muted">{c.split}</td><td>{c.passed?<span className="goal-pill good">Passed</span>:<span className="goal-pill bad">Failed</span>}</td>
        <td><div className="limit-chips">{c.metrics.map(m=><span key={m.metric} className={`limit-chip ${m.passed?'':'failed'}`} title={m.reason}>{m.metric.replace(' [GEval]','')}{m.score!=null&&m.judge?` ${m.score.toFixed(2)}`:''}</span>)}</div></td></tr>
        {open===c.suite+c.case&&<tr className="case-detail"><td colSpan="5"><div className="compare"><div><h4>Input</h4><p>{c.input}</p></div><div><h4>Expected</h4><p>{c.expected||'—'}</p></div><div className={c.passed?'':'failed'}><h4>Output</h4><p>{c.output||'—'}</p></div></div>
          {c.metrics.some(m=>!m.passed)&&<div className="compare-why"><h4>Why it failed</h4>{c.metrics.filter(m=>!m.passed).map(m=><p key={m.metric}><b>{m.metric}</b>: {m.reason}</p>)}</div>}
          {c.context?.length>0&&<details className="compare-facts"><summary>Facts the agent was given ({c.context.length})</summary><ul>{c.context.map((x,i)=><li key={i}>{x}</li>)}</ul></details>}</td></tr>}</React.Fragment>)}</tbody></table></div></section>
  </>;
}

const methodNames={hybrid:'Hybrid (foundation model)',trailing_mean:'Average (old method)'},methodColor={hybrid:'#2f9e78',trailing_mean:'#8a6fd1'};
/**
 * Admin Forecast accuracy: backtests of each forecasting method on synthetic people with a known future, and real
 * accuracy from customers' later transactions.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @returns Tab content.
 * Used in: Admin > Forecast accuracy (GET /ops/v1/forecast-accuracy, POST .../run, GET .../status).
 */
function OpsForecast({api}){
  const [run,setRun]=useState(''),[data,setData]=useState(null),[job,setJob]=useState(null),[error,setError]=useState(''),[hover,setHover]=useState(null);
  const load=id=>api(`/ops/v1/forecast-accuracy${id?`?run=${encodeURIComponent(id)}`:''}`).then(d=>{setData(d);setJob(d.job);}).catch(e=>setError(e.message));
  useEffect(()=>{load(run);},[run]);
  useEffect(()=>{if(job?.status!=='running')return;const t=setInterval(()=>api('/ops/v1/forecast-accuracy/status').then(j=>{setJob(j);if(j.status!=='running'){setRun(j.run_id);load(j.run_id);}}).catch(()=>{}),4000);return()=>clearInterval(t);},[job?.status]);
  async function start(){setError('');try{setJob(await api('/ops/v1/forecast-accuracy/run',{method:'POST'}));}catch(e){setError(e.message);}}
  if(!data)return error?<p className="error" role="alert">{error}</p>:<div className="empty"><FinMark animated/>Loading…</div>;
  const bt=data.backtest,rows=bt?.summary||[],horizons=[...new Set(rows.map(r=>r.horizon))],methods=[...new Set(rows.map(r=>r.method))];
  const cell=(m,h)=>rows.find(r=>r.method===m&&r.horizon===h);
  const grouped=(metric,unit,best,target)=>{const max=Math.max(...rows.map(r=>r[metric]||0),target||0,1)*1.1;
    return <div className="grouped">{horizons.map(h=><div key={h} className="group"><span className="group-label">{h} days</span>{methods.map(m=>{const r=cell(m,h);const id=`${metric}${m}${h}`;
      return <div key={m} className="gbar" tabIndex={0} onMouseEnter={()=>setHover(id)} onMouseLeave={()=>setHover(null)} onFocus={()=>setHover(id)} onBlur={()=>setHover(null)} aria-label={`${methodNames[m]}, ${h} days: ${r?.[metric]}${unit}`}>
        <span className="gtrack">{target!=null&&<b className="threshold" style={{left:`${target/max*100}%`}}/>}<i style={{width:`${Math.max((r?.[metric]||0)/max*100,1)}%`,background:methodColor[m]}}/></span><span className="cat-value">{unit==='₹'?`₹${Math.round(r?.[metric]||0).toLocaleString('en-IN')}`:`${r?.[metric]}${unit}`}</span>
        {hover===id&&<span className="cat-tip" role="tooltip"><strong>{methodNames[m]} · {h} days</strong>{unit==='₹'?`₹${Math.round(r[metric]).toLocaleString('en-IN')}`:`${r[metric]}${unit}`} over {r.cases} cases{best?` · ${best}`:''}</span>}</div>;})}</div>)}</div>;};
  const legend=<div className="legend-row">{methods.map(m=><span key={m}><i style={{background:methodColor[m]}}/>{methodNames[m]}</span>)}</div>;
  return <>
    <section className="panel"><div className="section-title"><div><h2>Forecast accuracy</h2><p className="muted">The forecaster's numbers are calculated, never written by an AI. Known income and bills are exact; everyday spending comes from {data.forecaster.model?<b>{data.forecaster.model}</b>:'the trailing average'}{data.forecaster.model?', a time-series foundation model with frozen weights':''}. Two ways to measure it: backtests on synthetic people with a known future, and real accuracy once customers' later transactions arrive.</p></div>
      <div className="button-row tight">{data.runs.length>1&&<select aria-label="Backtest run" value={run||bt?.meta.run_id} onChange={e=>setRun(e.target.value)}>{data.runs.map(r=><option key={r.run_id} value={r.run_id}>{r.run_id}</option>)}</select>}<button className="primary" disabled={job?.status==='running'} onClick={start}><Activity size={15}/>{job?.status==='running'?'Running backtest…':'Run backtest'}</button></div></div>
      {data.forecaster.error&&<p className="error">Model unavailable, so forecasts use the average: {data.forecaster.error}</p>}
      {job?.status==='running'&&<p className="muted small-text">Backtest {job.run_id} running · started by {job.by}{job.log_tail?.length?` · ${job.log_tail.at(-1)}`:''}</p>}
      {job?.status==='failed'&&<p className="error">The last backtest failed. {job.log_tail?.slice(-3).join(' ')}</p>}
      {error&&<p className="error" role="alert">{error}</p>}</section>
    {!bt?<section className="panel"><div className="empty"><LineChart size={28}/><h3>No backtest yet</h3><p>Run one to compare the methods.</p></div></section>:<>
    <div className="evals-grid">
      <section className="panel"><h2>Balance error</h2><p className="muted">Average gap between the forecast and the real balance on the last day. Lower is better.</p>{legend}{grouped('end_error','₹','lower is better')}</section>
      <section className="panel"><h2>Range reliability</h2><p className="muted">How often the real balance landed inside the 80% likely range. The mark is the 80% target: too low means overconfident, too high means vague.</p>{legend}{grouped('coverage_80','%','target 80%',80)}</section>
      <section className="panel"><h2>Spending error</h2><p className="muted">Median percentage gap between forecast and real everyday spending. Lower is better.</p>{legend}{grouped('spend_wape','%','lower is better')}</section>
      <section className="panel"><h2>Pinball loss</h2><p className="muted">Scores the whole range at once (10th, 50th, 90th percentiles). Lower is better.</p>{legend}{grouped('pinball','₹','lower is better')}</section>
    </div>
    <section className="panel"><h2>By persona</h2><p className="muted">Synthetic people with different habits, {bt.meta.cutoffs.length} forecast starts each. Run {bt.meta.run_id}{bt.meta.git_commit?` · commit ${bt.meta.git_commit}`:''}.</p>
      <div className="table-wrap"><table><thead><tr><th>Persona</th><th>Horizon</th>{methods.map(m=><th key={m} className="right">{methodNames[m]} error</th>)}{methods.map(m=><th key={m+'c'} className="right">{methodNames[m]} in range</th>)}</tr></thead><tbody>
        {Object.keys(bt.meta.personas).flatMap(name=>horizons.map(h=>{const r=m=>bt.personas.find(x=>x.persona===name&&x.method===m&&x.horizon===h);const errs=methods.map(m=>r(m)?.end_error??Infinity),best=Math.min(...errs);
          return <tr key={name+h}><td title={bt.meta.personas[name]}>{name.replace('_',' ')}</td><td className="muted">{h} days</td>{methods.map((m,i)=><td key={m} className={`right ${errs[i]===best?'best':''}`}>₹{Math.round(r(m)?.end_error||0).toLocaleString('en-IN')}</td>)}{methods.map(m=><td key={m+'c'} className="right muted">{r(m)?.coverage_80}%</td>)}</tr>;}))}
      </tbody></table></div></section></>}
    <section className="panel"><h2>Real accuracy</h2><p className="muted">When a customer's balance date moves past an earlier forecast, its everyday-spending forecast is compared with what really happened. Only the method and percentage error are kept: no amounts, no identity.</p>
      {!data.realized.by_method.length?<div className="empty"><TriangleAlert size={24}/><p>No forecasts have been scored yet. Scores appear as customers create new forecasts after earlier ones end.</p></div>:
      <div className="table-wrap"><table><thead><tr><th>Method</th><th className="right">Forecasts scored</th><th className="right">Median spending error</th><th className="right">Inside the likely range</th></tr></thead><tbody>
        {data.realized.by_method.map(m=><tr key={m.method}><td><span className="legend-dot" style={{background:methodColor[m.method]}}/>{methodNames[m.method]||m.method}{m.model?` · ${m.model}`:''}</td><td className="right">{m.count}</td><td className="right">{m.median_wape}%</td><td className="right">{m.band_hit_rate!=null?`${m.band_hit_rate}%`:'—'}</td></tr>)}
      </tbody></table></div>}</section>
  </>;
}

/**
 * The admin portal shell: header, tabs and the active tab.
 *
 * @param props.api api helper from useApi(): calls the backend with the session's CSRF token
 * @param props.onLogout sign out
 * @returns Page element.
 * Used in: /admin.
 */
function Ops({api,onLogout}){
  const [tab,setTab]=useState('overview');
  const tabs=[['overview','Overview',LayoutDashboard],['health','Alerts & health',TriangleAlert],['users','Users & activity',List],['abuse','Abuse & limits',ShieldCheck],['costs','AI costs',Wallet],['feedback','Feedback',ThumbsUp],['logs','Logs',Activity],['forecast','Forecast accuracy',BarChart3],['evals','Evaluations',LineChart],['audit','Audit log',Lock],['grafana','Grafana',BarChart3],['langsmith','LangSmith',Sparkles]];
  return <main className="ops"><header className="topbar"><div className="brand"><img className="brand-mark" src="/finpilot-mark.svg" alt=""/>FinPilot<span className="badge admin-badge">ADMIN</span></div><button className="secondary" onClick={onLogout}>Sign out<LogOut size={15}/></button></header><span className="eyebrow">OPERATIONS</span><h1>A view of the system.</h1><p className="muted">Alerts, health, costs, activity and logs. Customer data stays private: you see what happened, never what people entered.</p>
    <div className="ops-tabs" role="tablist">{tabs.map(([id,label,Icon])=><button key={id} role="tab" aria-selected={tab===id} className={tab===id?'active':''} onClick={()=>setTab(id)}><Icon size={15}/>{label}</button>)}</div>
    {tab==='overview'?<OpsOverview api={api}/>:tab==='health'?<OpsHealth api={api}/>:tab==='abuse'?<OpsAbuse api={api}/>:tab==='costs'?<OpsCosts api={api}/>:tab==='forecast'?<OpsForecast api={api}/>:tab==='evals'?<OpsEvals api={api}/>:tab==='audit'?<OpsAudit api={api}/>:tab==='users'?<OpsUsers api={api}/>:tab==='feedback'?<OpsFeedback api={api}/>:tab==='logs'?<OpsLogs api={api}/>:tab==='grafana'?<OpsGrafana api={api}/>:<OpsLangSmith api={api}/>}</main>;
}

/**
 * Hook returning an API caller that adds the CSRF token, raises readable errors and triggers celebrations.
 * @returns [api, csrfRef].
 * Used in: Customer app and admin portal.
 */
function useApi(){
  const csrf=useRef('');
  async function api(path,options={}){const res=await fetch(path,{credentials:'same-origin',...options,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.current,...options.headers}});if(res.status===204)return null;const data=await res.json().catch(()=>null);if(!res.ok)throw Error(typeof data?.detail==='string'?data.detail:data?.detail?JSON.stringify(data.detail):`Request failed (${res.status}). Is the API server running on port 8010?`);if(data?.unlocked?.length)window.dispatchEvent(new CustomEvent('fina-unlocked',{detail:data.unlocked}));return data;}
  return [api,csrf];
}

/**
 * The /admin application: session check, operator sign-in, then the portal.
 * @returns App element.
 * Used in: /admin.
 */
function AdminApp(){
  const [api,csrf]=useApi(),[session,setSession]=useState(null),[checking,setChecking]=useState(true);
  function auth(s){csrf.current=s.csrf_token;setSession(s);}
  useEffect(()=>{api('/ops/v1/auth/session').then(auth).catch(()=>{}).finally(()=>setChecking(false));},[]);
  async function logout(){try{await api('/ops/v1/auth/logout',{method:'POST'});}finally{csrf.current='';setSession(null);}}
  if(checking)return <div className="loading"><FinMark animated/>Opening the admin portal…</div>;
  return session?<Ops api={api} onLogout={logout}/>:<Auth api={api} onAuth={auth} admin/>;
}

/**
 * Chooses the admin portal for /admin, else the customer app.
 * @returns App element.
 * Used in: main.jsx.
 */
export default function Root(){
  return window.location.pathname.replace(/\/+$/,'')==='/admin'?<AdminApp/>:<App/>;
}

/**
 * The customer application: session, data loading, navigation and every page.
 * @returns App element.
 * Used in: Root.
 */
function App(){
  const [session,setSession]=useState(null),[checking,setChecking]=useState(true),[page,setPage]=useState('overview'),[profile,setProfile]=useState(null),[version,setVersion]=useState(0),[txs,setTxs]=useState([]),[total,setTotal]=useState(0),[run,setRun]=useState(null),[scenario,setScenario]=useState(null),[horizon,setHorizon]=useState(30),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false),[modal,setModal]=useState(null),[fixing,setFixing]=useState(null),[aiLimit,setAiLimit]=useState(20),[aiDaily,setAiDaily]=useState(20),[aiReady,setAiReady]=useState(false),[scenarioInput,setScenarioInput]=useState(null),[goalsKey,setGoalsKey]=useState(0),[txFilter,setTxFilter]=useState(null),[reroute,setReroute]=useState(null);
  const [api,csrf]=useApi();
  function auth(s){csrf.current=s.csrf_token;setSession(s);setError('');}
  useEffect(()=>{api('/api/v1/auth/session').then(auth).catch(()=>{}).finally(()=>setChecking(false));},[]);
  async function refresh(){const [p,t,h]=await Promise.all([api('/api/v1/profile'),api('/api/v1/transactions'),api('/health')]);setAiReady(h.ai_enabled);setProfile(p.profile);setVersion(p.version);setTxs(t.items);setTotal(t.total);setAiDaily(t.ai_daily_limit??20);setAiLimit(Math.max((t.ai_daily_limit??20)-(t.ai_used_today??0),0));setRun(null);setScenario(null);setScenarioInput(null);}
  useEffect(()=>{if(session?.role==='customer')refresh().catch(e=>setError(e.message));},[session]);
  useEffect(()=>{if(page!=='transactions')setTxFilter(null);},[page]);
  async function action(fn){setBusy(true);setError('');setNotice('');try{await fn();}catch(e){setError(e.message);}finally{setBusy(false);}}
  async function logout(){await action(async()=>{await api('/api/v1/auth/logout',{method:'POST'});setSession(null);csrf.current='';setProfile(null);setTxs([]);setRun(null);setScenario(null);});}
  async function save(p){await action(async()=>{await api('/api/v1/profile',{method:'PUT',body:JSON.stringify({profile:p,expected_version:version})});await refresh();setNotice('Your context is saved. Refresh the forecast to use it.');});}
  async function forecast(){await action(async()=>{setRun(await api('/api/v1/forecasts',{method:'POST',body:JSON.stringify({horizon_days:horizon})}));setScenario(null);});}
  async function purchase(e){e.preventDefault();const d=Object.fromEntries(new FormData(e.currentTarget));await action(async()=>{setScenario(await api('/api/v1/scenarios',{method:'POST',body:JSON.stringify({baseline_id:run.id,amount_minor:amount(d.amount),currency:profile.currency,date:d.date,funding_account:'primary'})}));setScenarioInput({amount_minor:amount(d.amount),date:d.date});});}
  async function retryAI(){await action(async()=>{const r=await api('/api/v1/transactions/recategorize',{method:'POST',body:JSON.stringify({expected_version:version})});await refresh();setNotice(r.attempted?`AI categorized ${r.categorized} of ${r.attempted} transactions. Create a new forecast to use them.`:'Every transaction already has a category.');});}
  async function fixed(r){await refresh();const also=r.also_updated?` Also updated ${r.also_updated} similar transaction${r.also_updated===1?'':'s'} from the same merchant.`:'';setNotice(r.category_source==='owner'?`Saved as “${r.category}”.${also}`:r.category==='uncategorized'?'AI still could not tell. Try a more specific note, or set the category yourself.':`AI chose “${r.category}”.${also} Create a new forecast to use it.`);}
  async function demo(){await action(async()=>{const p={...initialProfile,coverage_confirmed:true,opening_balance_minor:12500000,reserve_minor:3000000,scheduled_flows:[{date:'2026-11-01',label:'Synthetic salary',amount_minor:8500000},{date:'2026-11-02',label:'Synthetic rent',amount_minor:-2800000}]};await api('/api/v1/profile',{method:'PUT',body:JSON.stringify({profile:p,expected_version:version})});const lines=['date,description,amount_minor,currency,kind,recurring'];for(let i=0;i<60;i++){const date=new Date('2026-08-20T12:00:00Z');date.setUTCDate(date.getUTCDate()+i);lines.push(`${iso(date)},${i%3===0?'Grocery market':'Cafe lunch'},-${i<30?24000+(i%4)*3000:32000+(i%4)*3000},INR,expense,false`);}const preview=await api('/api/v1/imports',{method:'POST',body:JSON.stringify({csv_text:lines.join('\n')})});await api(`/api/v1/imports/${preview.id}/confirm`,{method:'POST',body:JSON.stringify({expected_version:preview.version,acknowledge_possible_duplicates:false})});await refresh();setRun(await api('/api/v1/forecasts',{method:'POST',body:JSON.stringify({horizon_days:horizon})}));setNotice('Synthetic example loaded. All amounts are calculated from its transactions.');});}
  if(checking)return <div className="loading"><FinMark animated/>Opening your workspace…</div>;
  if(!session)return <Auth api={api} onAuth={auth}/>;
  const currency=profile?.currency||'INR', spending=txs.filter(t=>t.kind==='expense').reduce((sum,t)=>sum-t.amount_minor,0), f=run?.forecast;
  const openReroute=a=>{setReroute(a||0);setPage('goals');};
  const listed=txFilter?txs.filter(t=>t.kind==='expense'&&t.amount_minor<0&&txFilter.categories.includes(t.category||'uncategorized')&&(!txFilter.from||t.date>=txFilter.from)&&t.date<=txFilter.to):txs;
  async function finaChanged(kind){if(kind==='confirm_goal'||kind==='confirm_goal_saving'){setGoalsKey(k=>k+1);setNotice(kind==='confirm_goal'?'Goal added. Find it on the Goals page.':'Saved to your goal. Tick it on Goals once it is fully funded to earn FinaPoints.');return;}await refresh();setNotice(kind==='confirm_category'?'Categories updated. Create a new forecast to use them.':'Your plan is updated. Create a new forecast to use it.');}
  const voiceProps={api,profile,currency,setPage,openReroute,onChanged:finaChanged,openQuestions:()=>setModal('investigate')};
  const unresolved=txs.filter(t=>t.category_source==='unresolved'), retryable=aiReady&&aiLimit>0?unresolved.length:0;
  const cashAt=row=>row.cash_minor??row.cumulative_flow_minor;
  const nav=[['overview','Overview',LayoutDashboard],['transactions','Transactions',List],['context','Your context',Settings2],['goals','Goals',Target],['outlook','Outlook',TriangleAlert],['invest','Invest',LineChart],['activity','Agent activity',Activity],['preferences','Preferences',ShieldCheck],['referral','Referral',Gift]];
  return <div className="shell"><aside className="sidebar"><div className="brand"><img className="brand-mark" src="/finpilot-mark.svg" alt=""/>FinPilot</div><div className="workspace-label">YOUR WORKSPACE</div><nav>{nav.map(([id,title,Icon])=><button key={id} className={page===id?'active':''} onClick={()=>setPage(id)}><Icon size={19}/>{title}{page===id&&<span className="nav-dot"/>}</button>)}</nav><div className="sidebar-note"><span className="little-star">✳</span><h4>A little ahead.<br/>A lot more clarity.</h4><p>Your next decision starts with a clearer picture.</p><span className="badge">EARLY ACCESS</span></div><button className="account" onClick={logout}><span className="avatar">{session.username[0].toUpperCase()}</span><span><strong>{session.username}</strong><small>Personal workspace</small></span><LogOut size={16}/></button></aside><main className="main"><header className="topbar"><div className="breadcrumb">Workspace<ChevronRight size={14}/><strong>{nav.find(n=>n[0]===page)[1]}</strong></div><span className="status-pill"><i/>Local development</span></header><div className="content"><section className="page-heading"><div><span className="eyebrow">MAKE ROOM FOR WHAT’S NEXT</span><h1>{page==='overview'?'Your financial horizon.':page==='transactions'?'Every transaction, in view.':page==='context'?'Start with your reality.':page==='goals'?'Make room for what matters.':page==='outlook'?'Where this is heading.':page==='preferences'?'Your data, your call.':page==='referral'?'Small wins, real rewards.':page==='invest'?'Grow it, carefully.':'See how it comes together.'}</h1><p className="muted">{page==='overview'?'A clearer picture of today. A thoughtful look at tomorrow.':page==='transactions'?'The details behind your bigger picture.':page==='context'?'A few confirmed facts make the numbers more useful.':page==='goals'?'Set a target, track it honestly, and let Fina help along the way.':page==='outlook'?'Spending that is creeping up, what it means for your goals if it continues, and what would help.':page==='preferences'?'Choose what, if anything, you share to help test and improve Fina.':page==='referral'?'Earn FinaPoints for reaching goals and inviting friends, and swap them for vouchers that fit how you spend.':page==='invest'?'Whether investing fits your situation yet, how much is sensible, and what to learn about first.':'Follow each agent from your transactions to your forecast.'}</p></div><button className="primary" disabled={!profile||busy} onClick={()=>setModal('import')}><FileUp size={16}/>Import transactions</button></section>{error&&<div className="error banner" role="alert">{error}<button onClick={()=>setError('')} aria-label="Dismiss error"><X size={16}/></button></div>}{notice&&<div className="success banner" role="status"><Check size={17}/>{notice}</div>}
  {page==='invest'?<Invest api={api} setPage={setPage}/>:page==='preferences'?<Preferences api={api}/>:page==='referral'?<Referral api={api}/>:page==='outlook'?<Outlook api={api} setPage={setPage}/>:page==='goals'?<><VoiceAsk {...voiceProps} page="goals" title="Tell Fina about a goal" examples={['save 60,000 for a Goa trip by March','I put 5,000 into my laptop goal']}/><Goals api={api} currency={currency} setPage={setPage} reloadKey={goalsKey} reroute={reroute} setReroute={setReroute}/></>:page==='context'?<>{profile&&missingFacts(profile).length>0&&<div className="attention fina-ask"><div className="inv-head"><span className="fina-avatar" aria-hidden="true">F</span><div><strong>Not sure what to fill in?</strong><p>Fina can look at your history and suggest your regular income and bills.</p></div></div><button className="secondary" onClick={()=>setModal('investigate')}><Sparkles size={15}/>Ask Fina what's missing</button></div>}<VoiceAsk {...voiceProps} page="context" title="Tell Fina instead of typing" examples={['my salary of 80,000 comes on the 1st','I have 45,000 in my account']}/><ContextForm profile={profile} save={save} busy={busy}/></>:page==='transactions'?<section className="panel"><div className="section-title"><div><h2>Your transaction history</h2><p className="muted">{total} transactions · {currency}{total>txs.length?` · Showing first ${txs.length}`:''}</p></div><button disabled={!profile} className="secondary" onClick={()=>setModal('manual')}><Plus size={16}/>Add transaction</button></div>{unresolved.length>0&&<div className="attention"><div><strong>{unresolved.length} transactions need a category.</strong><p>{aiReady&&!aiLimit?`You've used today's ${aiDaily} AI requests. `:''}{retryable?'Retry with AI, or click':'Click'} any category to set it yourself.</p></div>{retryable>0&&<button className="secondary" disabled={busy} onClick={retryAI}><Sparkles size={15}/>Retry with AI ({retryable})</button>}</div>}{txFilter&&<div className="tx-filter" role="status"><span className="filter-chip">{txFilter.label}<button aria-label="Clear filter" onClick={()=>setTxFilter(null)}><X size={13}/></button></span><span className="muted">Spending, {txFilter.period} ({txFilter.from||'start'} to {txFilter.to}) · {listed.length} transaction{listed.length===1?'':'s'} · {money(-listed.reduce((s,t)=>s+t.amount_minor,0),currency)}</span><button className="text-button" onClick={()=>setTxFilter(null)}>Show all</button></div>}{!txs.length?<div className="empty"><Wallet size={32}/><h3>Your story starts here.</h3><p>Save your context, then add a transaction or import a CSV.</p><button className="text-button" onClick={()=>setPage('context')}>Set up context <ArrowRight size={15}/></button></div>:<div className="table-wrap"><table><thead><tr><th>Date</th><th>Description</th><th>Category</th><th>Type</th><th className="right">Amount</th></tr></thead><tbody>{[...listed].sort((a,b)=>b.date.localeCompare(a.date)).map(t=><tr key={t.id}><td className="muted">{t.date}</td><td><strong>{t.description}</strong></td><td><button title={`${categoryReason(t)} Click to change.`} onClick={()=>setFixing(t)} className={`category ${t.category==='uncategorized'?'unresolved':''}`}>{t.category}</button></td><td className="muted">{t.kind}</td><td className={`right amount ${t.amount_minor>0?'positive':''}`}>{money(t.amount_minor,t.currency)}</td></tr>)}</tbody></table></div>}</section>:page==='activity'?<Workflow txs={txs} total={total} run={run} busy={busy} retryAI={retryAI} setPage={setPage} limit={aiLimit} aiReady={aiReady} profile={profile} askFina={()=>setModal('investigate')} api={api}/>:<>
    {!profile||!total?<div className="welcome panel"><div><span className="eyebrow">LET’S FIND YOUR STARTING POINT</span><h2>A fresh view begins<br/>with a few details.</h2><p className="muted">Set your financial context and bring in your transactions.<br/>Or explore a synthetic example to see how it works.</p><div className="button-row"><button className="primary" onClick={()=>setPage('context')}>Set up my context<ArrowRight size={16}/></button>{!total&&<button className="secondary" disabled={busy} onClick={demo}><Sparkles size={16}/>Explore an example</button>}</div></div><div className="welcome-art" aria-hidden="true"><div className="orbit one"/><div className="orbit two"/><div className="orbit three"/><Compass size={70}/><span className="art-tag">A clearer direction</span></div></div>:<>
    {missingFacts(profile).length>0&&<div className="attention fina-ask"><div className="inv-head"><span className="fina-avatar" aria-hidden="true">F</span><div><strong>Fina has a few questions</strong><p>Your forecast is missing {missingFacts(profile).join(', ')}. A minute of answers makes it much sharper.</p></div></div><button className="primary" onClick={()=>setModal('investigate')}><Sparkles size={15}/>Answer Fina's questions</button></div>}
    <div className="stats"><div className="stat"><span><Wallet size={17}/>Starting cash</span><strong>{money(profile.opening_balance_minor,currency)}</strong><small>As of {profile.as_of}</small></div><div className="stat"><span><ArrowDownLeft size={17}/>Recorded spending</span><strong>{money(spending,currency)}</strong><small>{total>txs.length?'First 1,000 rows only':`${total} transactions in your ledger`}</small></div><div className="stat highlight"><span><BarChart3 size={17}/>{f?.basis==='net_cash_flow'?'Projected net flow':'Projected ending cash'}</span><strong>{f?money(f.timeline.at(-1).cash_minor??f.timeline.at(-1).cumulative_flow_minor,currency):'Ready to calculate'}</strong><small>{f?`${f.timeline.at(-1).date} · provisional baseline`:'Create your first forecast below'}</small></div></div>
    <CategorySpend txs={txs} asOf={profile.as_of} currency={currency} onPick={f=>{setTxFilter(f);setPage('transactions');window.scrollTo({top:0});}}/>
    <div className="dashboard-grid"><section className="panel forecast-panel"><div className="section-title"><div><h2>Your next chapter, in numbers</h2><p className="muted">{f?.basis==='net_cash_flow'?'Cumulative projected net cash flow':'Your projected daily cash balance'}</p></div><div className="forecast-controls"><select aria-label="Forecast horizon" value={horizon} onChange={e=>setHorizon(Number(e.target.value))}>{[30,60,90].map(n=><option key={n} value={n}>{n} days</option>)}</select><button className="icon-button refresh" disabled={busy} onClick={forecast} aria-label="Calculate forecast"><RefreshCw size={17} className={busy?'spin':''}/></button></div></div>{f?<><div className="legend"><span><i/>Baseline</span>{scenario&&<span><i className="amber"/>With purchase</span>}<span className="legend-note">Not a guarantee</span></div><Chart baseline={f} scenario={scenario} currency={currency}/><div className="chart-foot"><span><ShieldCheck size={15}/>Calculated from your records</span><span>{f.history_days} days of history</span></div></>:<div className="empty"><BarChart3 size={36}/><h3>See where your money is heading.</h3><p>Project your daily cash flow from your history and planned events.</p><button className="primary" disabled={busy} onClick={forecast}>Create forecast<ArrowRight size={15}/></button></div>}</section>
    <section className="panel scenario-panel"><span className="feature-icon"><Compass size={22}/></span><span className="eyebrow">BEFORE YOU SAY YES</span><h2>What if?</h2><p className="muted">A trip. A new laptop. See how a purchase changes your trajectory.</p><form onSubmit={purchase}><label>Purchase amount ({currency})<input type="number" name="amount" required min=".01" step=".01" placeholder="e.g. 15000" disabled={!run}/></label><label>When would you pay?<input type="date" name="date" required min={f?.timeline[0].date} max={f?.timeline.at(-1).date} disabled={!run}/></label><button className="primary" disabled={!run||busy}>Explore the impact<ArrowUpRight size={16}/></button></form>{!run&&<p className="small-text muted">Create a baseline forecast first.</p>}{scenario&&<div className="scenario-result"><span>{f.basis==='net_cash_flow'?'Net flow':'Ending cash'} on {scenario.timeline.at(-1).date} with this purchase</span><strong>{money(cashAt(scenario.timeline.at(-1)),currency)}</strong><p>{money(cashAt(f.timeline.at(-1)),currency)} without it, so {money(-scenario.ending_delta_minor,currency)} less. Spending always lowers the ending balance by the amount spent.</p><p>{scenario.first_negative_date?`Your balance goes below zero on ${scenario.first_negative_date}.`:scenario.first_reserve_breach?`Your balance goes below your reserve on ${scenario.first_reserve_breach}.`:scenario.minimum_cash_minor==null?'Add your available cash in Your context to check balances.':profile.reserve_minor==null?`Lowest balance ${money(scenario.minimum_cash_minor,currency)}. Add a reserve in Your context to check against it.`:`Lowest balance ${money(scenario.minimum_cash_minor,currency)}, which stays ${money(scenario.minimum_cash_minor-profile.reserve_minor,currency)} above your reserve.`}</p><small>This simulation is not an affordability verdict.</small>{(scenario.first_reserve_breach||scenario.first_negative_date)&&<OptionsButton api={api} run={run} scenarioInput={scenarioInput}/>}</div>}</section></div>
    {f&&<div className="bottom-grid"><section className="panel"><div className="section-title"><h2>A pattern worth a look</h2><span className="badge">FROM YOUR HISTORY</span></div>{run.analysis.comparisons.length?<div className="patterns">{run.analysis.comparisons.filter(c=>c.current_minor||c.prior_minor).slice(0,3).map(c=><div className="pattern" key={c.category}><span className="pattern-icon"><BarChart3 size={18}/></span><div><h3>{c.category}</h3><p>{money(c.current_minor,currency)} in the latest 30 days, versus {money(c.prior_minor,currency)} before.</p></div><span className="delta">{c.percent_change==null?'New':`${c.percent_change>0?'+':''}${c.percent_change}%`}</span></div>)}</div>:!f.coverage_confirmed?<p className="muted">To compare your latest 30 days with the 30 before, confirm your history is complete. <button className="text-button inline" onClick={()=>setPage('context')}>Tick “I have included all transactions” in Your context</button>, then create a new forecast.</p>:f.history_days<60?<p className="muted">Comparisons need at least 60 days of history; you have {f.history_days}. Move “History starts” earlier in Your context if you have older transactions.</p>:<p className="muted">No spending in this period to compare yet.</p>}</section><section className="panel notes-panel"><span className="eyebrow">KEEP THE WHOLE PICTURE</span><h2>What this view assumes</h2><ul>{f.limitations.map(l=><li key={l}>{l}</li>)}</ul></section></div>}
    </>}
  </>}
  <footer className="footer"><span><Compass size={14}/> A clearer view, one step at a time.</span><span>FinPilot · Development preview</span></footer></div></main>{modal==='import'&&<ImportModal close={()=>setModal(null)} api={api} version={version} onDone={refresh} currency={currency}/>} {modal==='manual'&&<ManualModal close={()=>setModal(null)} api={api} currency={currency} onDone={refresh}/>}<Celebration/><ChatWidget api={api} run={run} scenarioInput={scenarioInput} currency={currency} setPage={setPage} profile={profile} openQuestions={()=>setModal('investigate')} openReroute={openReroute} onChanged={finaChanged}/>{modal==='investigate'&&profile&&<InvestigatorModal close={()=>setModal(null)} api={api} profile={profile} version={version} currency={currency} onSaved={async n=>{await refresh();setNotice(`Saved ${n} answer${n===1?'':'s'}. Create a new forecast to use them.`);}}/>}{fixing&&<CategoryModal tx={fixing} close={()=>setFixing(null)} api={api} version={version} onDone={fixed} currency={currency} limit={aiLimit} aiReady={aiReady}/>}</div>;
}
