import { expect, test } from "@playwright/test";

test("hidden pages release streams and returning to the same live scope retains verified numbers", async ({ page, isMobile }) => {
  const history=[];
  const active=new Map();
  const timers=[];
  let dashboardReads=0;
  let suppressRisk=false;
  let currentRisk;
  const risk=() => ({status:"connected",generated_at:new Date().toISOString(),source_updated_at:Date.now()/1000,
    summary:{active_accounts:1,open_positions:1,currency_totals:{USD:{accounts:1,balance:100,equity:112.34,
      margin:5,margin_free:107.34,floating_pnl:12.34}}},accounts:[],positions:[],symbols:[],warnings:[],network:{}});
  const dashboard={status:"connected",last_update:Date.now()/1000,errors:[],metrics:{},users:[],summary_rows:[],exposure_rows:[],
    totals:{visible_accounts:1,enabled_accounts:1,open_positions:1,currency_totals:{USD:{accounts:1,balance:100,equity:110,margin:5,margin_free:105}}}};
  const reply=(path) => {
    if(path.includes("/mt5/groups")) return {groups:["fixture"]};
    if(path.includes("/settings/books")) return {a_book:["fixture"],b_book:[],cent:[],hybrid:[]};
    if(path.includes("/manager/dashboard")) { dashboardReads++; return dashboard; }
    if(path.includes("/manager/status")) return {status:"connected",last_update:Date.now()/1000};
    if(path.includes("/risk/live")) return risk();
    return {};
  };
  await page.addInitScript(() => localStorage.setItem("user",JSON.stringify({id:1,username:"fixture",is_admin:true,must_change_pw:false})));
  await page.route("**/api/**",(route) => route.fulfill({json:reply(new URL(route.request().url()).pathname)}));
  await page.routeWebSocket("**/api/live/ws",(socket) => {
    timers.push(setInterval(() => {
      for(const [id,entry] of active) if(entry.socket===socket) socket.send(JSON.stringify({type:"progress",id,version:1}));
    },1000));
    socket.onMessage((raw) => {
      const message=JSON.parse(String(raw));
      if(message.type==="request") socket.send(JSON.stringify({type:"response",id:message.id,status:200,data:reply(message.path)}));
      if(message.type==="subscribe") {
        const entry={socket,topic:message.topic,id:message.id};
        active.set(message.id,entry); history.push({type:"subscribe",topic:message.topic});
        if(message.topic==="dashboard") socket.send(JSON.stringify({type:"snapshot",id:message.id,version:1,data:dashboard}));
        if(message.topic==="live-risk") {
          currentRisk=entry;
          if(!suppressRisk) socket.send(JSON.stringify({type:"snapshot",id:message.id,version:1,data:risk()}));
        }
      }
      if(message.type==="unsubscribe") {
        history.push({type:"unsubscribe",topic:active.get(message.id)?.topic}); active.delete(message.id);
      }
    });
  });
  await page.routeWebSocket("**/api/operations/ws",() => {});
  const navigate=async (name) => {
    if(isMobile) await page.getByRole("button",{name:"Open navigation"}).click();
    await page.getByRole("link",{name,exact:true}).click();
  };
  try {
    await page.goto("/manager");
    await expect(page.getByLabel("Manager money currency")).toHaveValue("USD");
    await navigate("Dealing and Risk");
    const floating=page.locator(".risk-stat-card").filter({has:page.getByText("Floating P&L",{exact:true})});
    await expect(floating).toContainText("12.34");
    await expect.poll(() => history.some((entry) => entry.type==="unsubscribe" && entry.topic==="dashboard")).toBeTruthy();
    const reads=dashboardReads;
    await page.waitForTimeout(3300);
    expect(dashboardReads).toBe(reads);
    await floating.evaluate((node) => { node.dataset.mountMarker="retained"; });
    suppressRisk=true;
    await navigate("Controls");
    await expect.poll(() => history.some((entry) => entry.type==="unsubscribe" && entry.topic==="live-risk")).toBeTruthy();
    await navigate("Dealing and Risk");
    await expect(floating).toContainText("12.34");
    expect(await floating.getAttribute("data-mount-marker")).toBe("retained");
    const latest=risk(); latest.summary.currency_totals.USD.floating_pnl=56.78;
    currentRisk.socket.send(JSON.stringify({type:"snapshot",id:currentRisk.id,version:1,data:latest}));
    await expect(floating).toContainText("56.78");
  } finally {
    for(const timer of timers) clearInterval(timer);
  }
});
