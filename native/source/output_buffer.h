#pragma once
#include <deque>
#include "pending_events.h"

// The UI needs latest entity state, not a replay of obsolete intermediate P&L.
// Preserve boot snapshots, deal events and all observation/clean barriers.
// The owning Output supplies synchronization.
class NativeOutputBuffer {
  bool boot=true;
  std::deque<std::string> snapshots;
  PendingEvents initial,live;
public:
  void push(const std::string& data,bool callback,const std::string& key,bool barrier){
    if(boot){if(callback)initial.push(data,key,barrier);else snapshots.push_back(data);}
    else live.push(data,key,barrier);
  }
  void begin(){
    for(auto& data:live.drain())snapshots.push_back(std::move(data));
    boot=true;initial.clear();
  }
  void complete(const std::string& end){
    for(auto& data:initial.drain())snapshots.push_back(std::move(data));
    snapshots.push_back(end);boot=false;
  }
  bool ready()const{return !snapshots.empty()||(!boot&&live.size());}
  size_t size()const{return snapshots.size()+initial.size()+live.size();}
  std::vector<std::string> take(size_t limit){
    std::vector<std::string> result;
    while(!snapshots.empty()&&result.size()<limit){result.push_back(std::move(snapshots.front()));snapshots.pop_front();}
    if(!boot&&result.size()<limit){auto tail=live.drain(limit-result.size());for(auto& data:tail)result.push_back(std::move(data));}
    return result;
  }
};
