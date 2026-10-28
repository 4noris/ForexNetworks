//go:build ignore
#include "output_buffer.h"
#include <cassert>
#include <iostream>

int main(){
  NativeOutputBuffer buffer;
  buffer.push("start",false,"",true);
  buffer.push("profile",false,"users:7",false);
  buffer.push("old",true,"positions:1",false);
  buffer.push("latest",true,"positions:1",false);
  assert((buffer.take(10)==std::vector<std::string>{"start","profile"}));
  assert(!buffer.ready());
  buffer.complete("complete");
  assert((buffer.take(10)==std::vector<std::string>{"latest","complete"}));
  for(int i=0;i<1000000;i++)buffer.push(std::to_string(i),true,"positions:"+std::to_string(i%11000),false);
  assert(buffer.size()==11000);
  buffer.push("deleted",true,"positions:10",false);
  auto front=buffer.take(512);assert(front.size()==512&&buffer.size()==10488);
  buffer.push("newest",true,"positions:10999",false);
  auto rest=buffer.take(20000);assert(rest.size()==10488&&rest.back()=="newest");
  bool deletion=false;for(const auto& data:rest)if(data=="deleted")deletion=true;assert(deletion);
  buffer.push("before",true,"positions:7",false);
  buffer.push("replace",false,"",true);
  buffer.push("after",true,"positions:7",false);
  buffer.push("cycle-end",false,"",true);
  buffer.push("new-cycle",true,"positions:7",false);
  assert((buffer.take(10)==std::vector<std::string>{"before","replace","after","cycle-end","new-cycle"}));
  buffer.push("deal add",true,"",false);buffer.push("deal update",true,"",false);
  buffer.push("before clean",true,"positions:7",false);buffer.push("clean",true,"",true);
  buffer.push("after clean",true,"positions:7",false);
  assert((buffer.take(10)==std::vector<std::string>{"deal add","deal update","before clean","clean","after clean"}));
  // Draining an old segment must not erase the newer segment's live-key index.
  buffer.push("old",true,"positions:1",false);buffer.push("barrier",false,"",true);
  buffer.push("middle",true,"positions:1",false);buffer.take(1);
  buffer.push("final",true,"positions:1",false);
  assert((buffer.take(10)==std::vector<std::string>{"barrier","final"}));
  std::cout<<"Native output tests passed: million-update burst, deletion, partial drain, boot, cycle and clean barriers, lossless deals\n";
}
