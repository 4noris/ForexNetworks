import { expect, test } from "@playwright/test";

test("risk center saves monitoring limits and retains drafts during live updates",async({page},testInfo)=>{
  let limits=[];
  let documents=0;
  page.on("request",(request)=>{if(request.isNavigationRequest()&&request.frame()===page.mainFrame())documents+=1;});
  await page.addInitScript(()=>localStorage.setItem("user",JSON.stringify({id:1,username:"test",is_admin:true,must_change_pw:false})));
  await page.route("**/api/**",async(route)=>{
    const path=new URL(route.request().url()).pathname;
    const method=route.request().method();
    let body={};
    if(path.endsWith("/dealing-risk/health"))body={connected:true,stale:false,data_age_seconds:1,groups:["real"],enabled_limits:limits.length,open_high_risk_events:2,pending_approvals:0,group_mismatches:[],checks:[{id:"connection",label:"MT5 connection",state:"ok",value:"Connected",href:"/manager"}]};
    if(path.endsWith("/operations/limits")){
      if(method==="POST"){body={...JSON.parse(route.request().postData()),id:1};limits=[body];}else body={limits};
    }
    if(path.endsWith("/operations/limits/1")){body={...JSON.parse(route.request().postData()),id:1};limits=[body];}
    if(path.endsWith("/operations/live"))body={status:"connected",limits:limits.map((row)=>({...row,current_value:0.5,evaluation_state:"within_limit"}))};
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify(body)});
  });
  await page.goto("/risk-center");
  await expect(page.getByRole("heading",{name:"Risk Center",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"Add limit"}).click();
  await page.getByLabel("Account login",{exact:true}).fill("1001");
  await page.getByLabel("Threshold",{exact:true}).fill("5");
  await page.waitForTimeout(5500);
  await expect(page.getByLabel("Threshold",{exact:true})).toHaveValue("5");
  await page.getByRole("button",{name:"Save",exact:true}).click();
  await expect(page.getByRole("cell",{name:"1001 account",exact:false})).toBeVisible();
  await page.getByRole("button",{name:"Edit",exact:true}).click();
  await page.getByLabel("Threshold",{exact:true}).fill("10");
  await page.getByRole("button",{name:"Save",exact:true}).click();
  await expect(page.getByRole("cell",{name:"10",exact:true})).toBeVisible();
  expect(limits[0].threshold).toBe(10);
  expect(documents).toBe(1);
  await expect(page).toHaveURL(/\/risk-center$/);
  expect(await page.locator("main").evaluate((node)=>node.scrollWidth<=node.clientWidth+1)).toBe(true);
  await page.screenshot({path:testInfo.outputPath("risk-center.png"),fullPage:true});
});
