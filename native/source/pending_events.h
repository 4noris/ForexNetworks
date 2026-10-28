#pragma once
#include <algorithm>
#include <iterator>
#include <limits>
#include <list>
#include <string>
#include <unordered_map>
#include <vector>
#include <utility>

// While the initial source snapshot loads, retain the latest state for each
// entity. Deletes use the same key as updates; deal events remain lossless.
// A clean-login operation is an ordering barrier and starts a new segment.
class PendingEvents {
  struct Row { std::string key, data; };
  std::list<Row> rows;
  std::unordered_map<std::string, std::list<Row>::iterator> latest;
public:
  void push(const std::string& data,const std::string& key,bool barrier=false){
    if(barrier)latest.clear();
    if(!key.empty()){
      auto old=latest.find(key);
      if(old!=latest.end()){rows.erase(old->second);latest.erase(old);}
    }
    rows.push_back({key,data});
    if(!key.empty())latest[key]=std::prev(rows.end());
  }
  size_t size()const{return rows.size();}
  void clear(){latest.clear();rows.clear();}
  std::vector<std::string> drain(size_t limit=std::numeric_limits<size_t>::max()){
    std::vector<std::string> result;result.reserve(std::min(rows.size(),limit));
    while(!rows.empty()&&result.size()<limit){
      auto first=rows.begin();
      auto indexed=latest.find(first->key);
      if(indexed!=latest.end()&&indexed->second==first)latest.erase(indexed);
      result.push_back(std::move(first->data));rows.erase(first);
    }
    return result;
  }
};
