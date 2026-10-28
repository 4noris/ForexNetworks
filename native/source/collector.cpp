//go:build ignore

// Read-only MT5 Manager collector. Credentials are inherited in environment,
// never accepted on the command line. stdout is an NDJSON IPC protocol only.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#include <cstdint>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <deque>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <locale>
#include <mutex>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>
#include <unordered_map>
#include "MT5APIManager.h"
#include "output_buffer.h"
#include "json_writer.h"

static int64_t now_ms() { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count(); }
static std::wstring env(const wchar_t* key) { DWORD n=GetEnvironmentVariableW(key,nullptr,0); if(!n)return L""; std::wstring s(n,L'\0'); GetEnvironmentVariableW(key,s.data(),n); s.resize(n-1); return s; }
static std::string utf8(const wchar_t* s) { if(!s)return ""; int n=WideCharToMultiByte(CP_UTF8,0,s,-1,nullptr,0,nullptr,nullptr); if(n<1)return ""; std::string r(n,'\0'); WideCharToMultiByte(CP_UTF8,0,s,-1,r.data(),n,nullptr,nullptr); r.pop_back(); return r; }
struct Json: NativeJson {
  void str(const char* key,const wchar_t* value){str8(key,utf8(value));}
};

class Output {
  std::mutex mutex; std::condition_variable cv; NativeOutputBuffer buffer;
public:
  void push(const std::string& s,bool event=true,const std::string& key="",bool barrier=false){
    std::lock_guard<std::mutex> lock(mutex);buffer.push(s,event,key,barrier);
    if(buffer.size()>200000){std::cerr<<"native event queue overflow; full resync required\n";std::_Exit(75);}
    cv.notify_one();
  }
  void begin(){std::lock_guard<std::mutex> lock(mutex);buffer.begin();}
  void complete(const std::string& end){std::lock_guard<std::mutex> lock(mutex);buffer.complete(end);cv.notify_one();}
  void writer(){for(;;){std::vector<std::string> batch;{std::unique_lock<std::mutex> lock(mutex);cv.wait(lock,[this]{return buffer.ready();});batch=buffer.take(512);}for(auto& s:batch)std::cout<<s<<'\n';std::cout.flush();if(!std::cout)std::_Exit(74);}}
};
static Output output;
static void emit(const char* kind,const std::string& id,const std::string& data,const char* op="upsert",bool callback=true){Json j;j.str8("kind",kind);j.str8("op",op);j.str8("id",id);j.num("received_ms",now_ms());j.raw("data",data);std::string family(kind),operation(op);auto key=(family!="deals"&&(operation=="upsert"||operation=="delete"))?family+":"+id:std::string();output.push(j.done(),callback,key,operation=="clean_login"||operation=="replace"||operation=="cycle_end"||family=="status");}
static std::string user_json(const IMTUser* u){Json j;MTAPISTR ip;j.num("Login",u->Login());j.str("Name",u->Name());j.str("Group",u->Group());j.str("Country",u->Country());j.str("Email",u->EMail());j.str("LastIP",u->LastIP(ip));j.num("Agent",u->Agent());j.boolean("Enabled",(u->Rights()&1)!=0);j.num("Leverage",u->Leverage());j.num("Balance",u->Balance());j.num("Credit",u->Credit());return j.done();}
static std::string account_json(const IMTAccount* a){Json j;j.num("Login",a->Login());j.num("Balance",a->Balance());j.num("Credit",a->Credit());j.num("Equity",a->Equity());j.num("Margin",a->Margin());j.num("Free Margin",a->MarginFree());j.num("Margin Level %",a->MarginLevel());j.num("Floating PnL",a->Profit());j.num("Swap",a->Storage());return j.done();}
static std::string position_json(const IMTPosition* p){Json j;j.num("Login",p->Login());j.str8("Ticket",std::to_string(p->Position()));j.str("Symbol",p->Symbol());j.num("Action",p->Action());j.num("Volume",double(p->VolumeExt())/100000000.0);j.num("PriceOpen",p->PriceOpen());j.num("PriceCurrent",p->PriceCurrent());j.num("ContractSize",p->ContractSize());j.num("RateProfit",p->RateProfit());j.num("Profit",p->Profit());j.num("Swap",p->Storage());j.num("TimeCreate",p->TimeCreate());return j.done();}
static std::string symbol_json(const IMTConSymbol* s){Json j;j.str("Symbol",s->Symbol());j.str("Description",s->Description());j.str("Path",s->Path());j.num("Digits",s->Digits());j.num("Point",s->Point());j.num("CalcMode",s->CalcMode());j.num("Contract Size",s->ContractSize());j.str("Currency Profit",s->CurrencyProfit());j.num("Tick Value",s->TickValue());j.num("Tick Size",s->TickSize());j.num("Margin Initial",s->MarginInitial());return j.done();}
static std::string group_json(const IMTConGroup* g){Json j;j.str("Group",g->Group());j.str("Currency",g->Currency());j.num("CurrencyDigits",g->CurrencyDigits());return j.done();}
static std::string summary_json(const IMTSummary* s){Json j;j.str("Symbol",s->Symbol());j.num("Digits",s->Digits());j.num("PositionClients",s->PositionClients());j.num("PositionCoverage",s->PositionCoverage());j.num("VolumeBuyClients",double(s->VolumeBuyClientsExt())/100000000.0);j.num("VolumeBuyCoverage",double(s->VolumeBuyCoverageExt())/100000000.0);j.num("VolumeSellClients",double(s->VolumeSellClientsExt())/100000000.0);j.num("VolumeSellCoverage",double(s->VolumeSellCoverageExt())/100000000.0);j.num("PriceBuyClients",s->PriceBuyClients());j.num("PriceBuyCoverage",s->PriceBuyCoverage());j.num("PriceSellClients",s->PriceSellClients());j.num("PriceSellCoverage",s->PriceSellCoverage());j.num("VolumeNet",s->VolumeNet());j.num("ProfitFullClients",s->ProfitFullClients());j.num("ProfitFullCoverage",s->ProfitFullCoverage());j.num("ProfitUncoveredFull",s->ProfitUncoveredFull());return j.done();}
static std::string exposure_json(const IMTExposure* e){Json j;j.str("Symbol",e->Symbol());j.num("Digits",e->Digits());j.num("VolumeClients",e->VolumeClients());j.num("VolumeCoverage",e->VolumeCoverage());j.num("VolumeNet",e->VolumeNet());j.num("PriceRate",e->PriceRate());return j.done();}
static void heartbeat(IMTManagerAPI* api){Json j;j.boolean("connected",true);auto clock=api->TimeServer();j.num("server_time_msc",clock>0&&clock<100000000000LL?clock*1000:clock);emit("status","",j.done(),"heartbeat",false);}

class Collector final: public IMTManagerSink, public IMTUserSink, public IMTPositionSink, public IMTTickSink, public IMTDealSink, public IMTAccountSink, public IMTConGroupSink, public IMTConSymbolSink, public IMTOrderSink {
public:
  std::atomic<bool> connected{false},resync{false}; std::mutex dirty_mutex;std::set<uint64_t> dirty;
  void changed(uint64_t login){std::lock_guard<std::mutex> lock(dirty_mutex);dirty.insert(login);}
  void OnConnect()override{connected=true;}
  void OnDisconnect()override{resync=true;connected=false;emit("status","", "{\"connected\":false}","status",false);}
  struct Profile {std::string payload;double balance,credit;uint64_t leverage,rights;std::wstring group;};
  std::unordered_map<uint64_t,Profile> profiles;
  void OnUserAdd(const IMTUser* u)override{
    auto data=user_json(u);bool send=false;
    {std::lock_guard<std::mutex> lock(dirty_mutex);auto old=profiles.find(u->Login());
      const bool financial=old==profiles.end()||old->second.balance!=u->Balance()||old->second.credit!=u->Credit()||
        old->second.leverage!=u->Leverage()||old->second.rights!=u->Rights()||old->second.group!=u->Group();
      if(financial)dirty.insert(u->Login());
      if(old==profiles.end()||old->second.payload!=data){profiles[u->Login()]={data,u->Balance(),u->Credit(),u->Leverage(),u->Rights(),u->Group()};send=true;}
    }
    if(send)emit("users",std::to_string(u->Login()),data);
  }
  void OnUserUpdate(const IMTUser* u)override{OnUserAdd(u);}
  void OnOrderAdd(const IMTOrder* o)override{changed(o->Login());}
  void OnOrderUpdate(const IMTOrder* o)override{changed(o->Login());}
  void OnOrderDelete(const IMTOrder* o)override{changed(o->Login());}
  void OnOrderClean(uint64_t login)override{changed(login);}
  void OnUserDelete(const IMTUser* u)override{{std::lock_guard<std::mutex> lock(dirty_mutex);profiles.erase(u->Login());dirty.erase(u->Login());}emit("users",std::to_string(u->Login()),"null","delete");}
  void OnUserClean(const uint64_t login)override{{std::lock_guard<std::mutex> lock(dirty_mutex);profiles.erase(login);dirty.erase(login);}emit("users",std::to_string(login),"null","delete");}
  void OnPositionAdd(const IMTPosition* p)override{changed(p->Login());emit("positions",std::to_string(p->Position()),position_json(p));}
  void OnPositionUpdate(const IMTPosition* p)override{OnPositionAdd(p);}
  void OnPositionDelete(const IMTPosition* p)override{changed(p->Login());emit("positions",std::to_string(p->Position()),"null","delete");}
  void OnPositionClean(const uint64_t login)override{changed(login);emit("positions",std::to_string(login),"null","clean_login");}
  void OnTick(LPCWSTR symbol,const MTTickShort& t)override{Json j;j.str("Symbol",symbol);j.num("Bid",t.bid);j.num("Ask",t.ask);j.num("Last",t.last);j.num("TimeMsc",t.datetime_msc);j.num("Time",t.datetime);j.num("Flags",t.flags);emit("ticks",utf8(symbol),j.done());}
  void OnDealAdd(const IMTDeal* d)override{changed(d->Login());Json j;j.num("Login",d->Login());j.str8("Deal",std::to_string(d->Deal()));j.str("Symbol",d->Symbol());j.num("TimeMsc",d->TimeMsc());emit("deals",std::to_string(d->Deal()),j.done());}
  void OnDealUpdate(const IMTDeal* d)override{OnDealAdd(d);}
  void OnAccountMarginCallEnter(const IMTAccount* a,const IMTConGroup*)override{emit("accounts",std::to_string(a->Login()),account_json(a));}
  void OnAccountMarginCallLeave(const IMTAccount* a,const IMTConGroup* g)override{OnAccountMarginCallEnter(a,g);}
  void OnAccountStopOutEnter(const IMTAccount* a,const IMTConGroup* g)override{OnAccountMarginCallEnter(a,g);}
  void OnAccountStopOutLeave(const IMTAccount* a,const IMTConGroup* g)override{OnAccountMarginCallEnter(a,g);}
  void OnGroupAdd(const IMTConGroup* g)override{emit("groups",utf8(g->Group()),group_json(g));}
  void OnGroupUpdate(const IMTConGroup* g)override{OnGroupAdd(g);}
  void OnGroupDelete(const IMTConGroup* g)override{emit("groups",utf8(g->Group()),"null","delete");}
  void OnSymbolAdd(const IMTConSymbol* s)override{emit("symbols",utf8(s->Symbol()),symbol_json(s));}
  void OnSymbolUpdate(const IMTConSymbol* s)override{OnSymbolAdd(s);}
  void OnSymbolDelete(const IMTConSymbol* s)override{emit("symbols",utf8(s->Symbol()),"null","delete");}
};
template<class T>struct Native{T* p;explicit Native(T* v):p(v){if(!p)throw std::runtime_error("SDK allocation failed");}~Native(){if constexpr(std::is_same_v<T,IMTManagerAPI>)p->Disconnect();p->Release();}T* operator->(){return p;}operator T*(){return p;}};
static bool empty_ok(MTAPIRES r){return r==MT_RET_OK||r==MT_RET_OK_NONE||r==MT_RET_ERR_NOTFOUND;}
static void check(MTAPIRES r,const char* operation){if(r!=MT_RET_OK){std::cerr<<operation<<" failed, code "<<r<<'\n';throw std::runtime_error(operation);}}
static void refresh_positions(IMTManagerAPI* api,std::set<uint64_t>& active,bool initial){Native<IMTPositionArray> positions(api->PositionCreateArray());auto started=now_ms();auto rc=initial?api->PositionRequestByGroup(L"*",positions):api->PositionGetByGroup(L"*",positions);
  if(!empty_ok(rc)){rc=api->PositionRequestByGroup(L"*",positions);if(!empty_ok(rc))check(rc,"PositionRequestByGroup");}Json rows;std::string list="[";active.clear();for(uint32_t i=0;i<positions->Total();i++){auto p=positions->Next(i);if(!p)throw std::runtime_error("Incomplete positions");if(i)list+=',';list+=position_json(p);active.insert(p->Login());}list+=']';Json frame;frame.str8("kind","positions");frame.str8("op","replace");frame.str8("id","");frame.num("received_ms",now_ms());frame.num("snapshot_started_ms",started);frame.raw("data",list);output.push(frame.done(),false);}
static void include_order_accounts(IMTManagerAPI* api,std::set<uint64_t>& active){
  Native<IMTOrderArray> orders(api->OrderCreateArray());auto rc=api->OrderGetByGroup(L"*",orders);
  if(!empty_ok(rc))check(rc,"Order cache read");
  for(uint32_t i=0;i<orders->Total();i++){auto o=orders->Next(i);if(!o)throw std::runtime_error("Incomplete order cache");active.insert(o->Login());}
}
static bool valid_account(const IMTAccount* a){
  return a&&std::isfinite(a->Balance())&&std::isfinite(a->Credit())&&std::isfinite(a->Equity())&&
    std::isfinite(a->Margin())&&std::isfinite(a->MarginFree())&&std::isfinite(a->MarginLevel())&&
    std::isfinite(a->Profit())&&std::isfinite(a->Storage());
}
static std::set<uint64_t> refresh_accounts(IMTManagerAPI* api,const std::set<uint64_t>& logins){
  std::set<uint64_t> fetched;
  std::vector<uint64_t> ids(logins.begin(),logins.end());
  constexpr size_t batch_size=1024;
  for(size_t offset=0;offset<ids.size();offset+=batch_size){
    const auto count=uint32_t(std::min(batch_size,ids.size()-offset));
    Native<IMTAccountArray> cached(api->UserCreateAccountArray());
    auto rc=api->UserAccountGetByLogins(ids.data()+offset,count,cached);
    if(empty_ok(rc))for(uint32_t i=0;i<cached->Total();i++){
      auto a=cached->Next(i);
      if(!valid_account(a)||!logins.count(a->Login()))continue;
      fetched.insert(a->Login());emit("accounts",std::to_string(a->Login()),account_json(a),"upsert",false);
    }
    std::vector<uint64_t> missing;
    for(size_t i=offset;i<offset+count;i++)if(!fetched.count(ids[i]))missing.push_back(ids[i]);
    // Read through to the server only for metrics not in the synchronized SDK
    // cache. Never infer equity/margin or substitute missing values with zero.
    if(!missing.empty()){
      Native<IMTAccountArray> requested(api->UserCreateAccountArray());
      rc=api->UserAccountRequestByLogins(missing.data(),uint32_t(missing.size()),requested);
      if(!empty_ok(rc))check(rc,"Account fallback read");
      for(uint32_t i=0;i<requested->Total();i++){
        auto a=requested->Next(i);
        if(!valid_account(a)||!logins.count(a->Login()))throw std::runtime_error("Invalid authoritative account metrics");
        fetched.insert(a->Login());emit("accounts",std::to_string(a->Login()),account_json(a),"upsert",false);
      }
    }
  }
  emit("accounts","","{}","cycle_end",false);
  return fetched;
}
static void refresh_panels(IMTManagerAPI* api){Native<IMTSummaryArray> rows(api->SummaryCreateArray());if(empty_ok(api->SummaryGetAll(rows))){std::string list="[";for(uint32_t i=0;i<rows->Total();i++){if(i)list+=',';list+=summary_json(rows->Next(i));}list+=']';emit("summary","",list,"replace",false);}Native<IMTExposureArray> exposure(api->ExposureCreateArray());if(empty_ok(api->ExposureGetAll(exposure))){std::string list="[";for(uint32_t i=0;i<exposure->Total();i++){if(i)list+=',';list+=exposure_json(exposure->Next(i));}list+=']';emit("exposure","",list,"replace",false);}}

int wmain(){SetErrorMode(SEM_FAILCRITICALERRORS|SEM_NOGPFAULTERRORBOX);std::thread(&Output::writer,&output).detach();auto server=env(L"MT5_MANAGER_SERVER"),password=env(L"MT5_MANAGER_PASSWORD"),login_text=env(L"MT5_MANAGER_LOGIN"),sdk=env(L"MT5_SDK_LIB_DIR");if(server.empty()||password.empty()||login_text.empty()||sdk.empty()){std::cerr<<"Missing native collector configuration\n";return 78;}uint64_t login=_wcstoui64(login_text.c_str(),nullptr,10);if(!login)return 78;
  for(;;){try{output.begin();emit("status","","{\"connected\":false}","reset",false);CMTManagerAPIFactory factory;check(factory.Initialize(sdk.c_str()),"SDK load");IMTManagerAPI* raw=nullptr;check(factory.CreateManager(MTManagerAPIVersion,&raw),"SDK create");Collector sink;Native<IMTManagerAPI> api(raw);
    check(api->Subscribe(&sink),"ManagerSubscribe");check(api->UserSubscribe(&sink),"UserSubscribe");check(api->PositionSubscribe(&sink),"PositionSubscribe");check(api->OrderSubscribe(&sink),"OrderSubscribe");check(api->TickSubscribe(&sink),"TickSubscribe");check(api->DealSubscribe(&sink),"DealSubscribe");check(api->UserAccountSubscribe(&sink),"AccountSubscribe");check(api->GroupSubscribe(&sink),"GroupSubscribe");check(api->SymbolSubscribe(&sink),"SymbolSubscribe");
    check(api->Connect(server.c_str(),login,password.c_str(),L"",IMTManagerAPI::PUMP_MODE_USERS|IMTManagerAPI::PUMP_MODE_POSITIONS|IMTManagerAPI::PUMP_MODE_ORDERS|IMTManagerAPI::PUMP_MODE_GROUPS|IMTManagerAPI::PUMP_MODE_SYMBOLS|IMTManagerAPI::PUMP_MODE_TIME,30000),"MT5 connect");sink.connected=true;
    std::cerr<<"Native bootstrap: loading group and symbol configuration\n";
    Native<IMTConGroup> group(api->GroupCreate());for(uint32_t i=0;i<api->GroupTotal();i++){check(api->GroupNext(i,group),"GroupNext");emit("groups",utf8(group->Group()),group_json(group),"upsert",false);}
    Native<IMTConSymbol> symbol(api->SymbolCreate());for(uint32_t i=0;i<api->SymbolTotal();i++){check(api->SymbolNext(i,symbol),"SymbolNext");emit("symbols",utf8(symbol->Symbol()),symbol_json(symbol),"upsert",false);}
    check(api->SelectedAddAll(),"SelectedAddAll");
    std::cerr<<"Native bootstrap: requesting account profiles\n";
    std::set<uint64_t> profile_ids;{Native<IMTUserArray> users(api->UserCreateArray());auto ur=api->UserGetByGroup(L"*",users);if(!empty_ok(ur)||users->Total()!=api->UserTotal()){ur=api->UserRequestArray(L"*",users);if(!empty_ok(ur))check(ur,"UserRequestArray");}for(uint32_t i=0;i<users->Total();i++){auto u=users->Next(i);if(!u)throw std::runtime_error("Incomplete user array");profile_ids.insert(u->Login());emit("users",std::to_string(u->Login()),user_json(u),"upsert",false);}
    } // Release the full SDK user array before account reads and live streaming.
    std::cerr<<"Native bootstrap: requesting positions\n";
    std::set<uint64_t> active;refresh_positions(api,active,true);std::cerr<<"Native bootstrap: requesting account metrics\n";
    // The initial metric request already covers users pumped before this point.
    // Keep callbacks arriving during the request dirty for the next live cycle.
    {std::lock_guard<std::mutex> lock(sink.dirty_mutex);sink.dirty.clear();}
    auto covered=refresh_accounts(api,profile_ids);
    std::set<uint64_t> missing;std::set_difference(profile_ids.begin(),profile_ids.end(),covered.begin(),covered.end(),std::inserter(missing,missing.end()));
    std::cerr<<"Native bootstrap coverage: profiles="<<profile_ids.size()<<" metrics="<<covered.size()<<" pending="<<missing.size()<<'\n';
    if(!missing.empty())refresh_accounts(api,missing);
    refresh_panels(api);Json end;end.str8("kind","status");end.str8("op","snapshot_end");end.raw("data","{\"connected\":true}");end.num("received_ms",now_ms());if(!sink.connected || sink.resync)throw std::runtime_error("Source disconnected during snapshot");output.complete(end.done());
    std::cerr<<"Native bootstrap complete; live pumping cache active\n";
    auto last=std::chrono::steady_clock::now(),last_panels=last,last_positions=last,last_diagnostic=last;
    while(sink.connected && !sink.resync){
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
      auto current=std::chrono::steady_clock::now();
      if(current-last<std::chrono::milliseconds(250))continue;
      auto cycle_started=now_ms();
      // Every subscribed position callback already carries authoritative live
      // state. A complete scan is a reconciliation, not a per-tick data source.
      if(current-last_positions>=std::chrono::seconds(2)){
        refresh_positions(api,active,false);include_order_accounts(api,active);
        last_positions=std::chrono::steady_clock::now();
      }
      {std::lock_guard<std::mutex> lock(sink.dirty_mutex);active.insert(sink.dirty.begin(),sink.dirty.end());sink.dirty.clear();}
      refresh_accounts(api,active);
      if(current-last_panels>=std::chrono::seconds(1)){refresh_panels(api);heartbeat(api);last_panels=std::chrono::steady_clock::now();}
      if(current-last_diagnostic>=std::chrono::seconds(10)){
        std::cerr<<"Native cache cycle: accounts="<<active.size()<<" elapsed_ms="<<(now_ms()-cycle_started)<<'\n';last_diagnostic=current;
      }
      last=std::chrono::steady_clock::now();
    }
    api->Disconnect();api->SymbolUnsubscribe(&sink);api->GroupUnsubscribe(&sink);api->UserAccountUnsubscribe(&sink);api->DealUnsubscribe(&sink);api->TickUnsubscribe(&sink);api->OrderUnsubscribe(&sink);api->PositionUnsubscribe(&sink);api->UserUnsubscribe(&sink);api->Unsubscribe(&sink);
  }catch(const std::exception&){emit("status","","{\"connected\":false}","status",false);std::cerr<<"Native feed unavailable; reconnecting with a full snapshot\n";}std::this_thread::sleep_for(std::chrono::seconds(3));}
}
