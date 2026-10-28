//go:build ignore

#include "pending_events.h"
#include <cassert>
#include <iostream>

int main(){
  PendingEvents events;
  for(int i=0;i<172000;i++)events.push("profile","users:"+std::to_string(i));
  for(int i=0;i<1000000;i++)events.push(std::to_string(i),"positions:"+std::to_string(i%11000));
  assert(events.size()==183000);
  events.push("closed","positions:1");
  auto initial=events.drain();
  assert(initial.size()==183000&&initial.back()=="closed");
  // A close must not be lost when state updates are coalesced.
  events.push("open","positions:9");events.push("delete","positions:9");
  auto closed=events.drain();assert(closed.size()==1&&closed[0]=="delete");
  // Keep clean-login ordering across both sides of the barrier.
  events.push("before","positions:9");events.push("clean","",true);events.push("after","positions:9");
  auto ordered=events.drain();assert((ordered==std::vector<std::string>{"before","clean","after"}));
  // Deal additions/updates remain independent notifications.
  events.push("deal add","");events.push("deal update","");
  assert(events.drain().size()==2);
  std::cout<<"Pending event tests passed: 172000 accounts and 1000000 updates\n";
}
