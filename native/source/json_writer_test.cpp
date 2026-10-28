//go:build ignore
#include "json_writer.h"
#include <cassert>
#include <chrono>
#include <iomanip>
#include <iostream>
#include <limits>
#include <locale>
#include <sstream>

enum class Value:unsigned{One=1};
int main(){
  NativeJson j;
  j.num("max",std::numeric_limits<unsigned long long>::max());
  j.num("min",std::numeric_limits<long long>::min());
  j.num("price",1.2345678901234567);j.num("tiny",std::numeric_limits<double>::denorm_min());
  j.num("nan",std::numeric_limits<double>::quiet_NaN());j.num("infinity",std::numeric_limits<double>::infinity());
  j.num("enum",Value::One);j.boolean("valid",true);
  j.str8("escaped","line\n\"quoted\"\\path\t");j.raw("raw","[1,null]");
  auto result=j.done();
  assert(result.find("18446744073709551615")!=std::string::npos);
  assert(result.find("-9223372036854775808")!=std::string::npos);
  assert(result.find("\"nan\":null")!=std::string::npos);
  assert(result.find("\\u000a")!=std::string::npos);
  std::cout<<result<<'\n';
  constexpr int count=100000;
  size_t old_bytes=0,new_bytes=0;
  auto start=std::chrono::steady_clock::now();
  for(int i=0;i<count;i++){
    std::ostringstream old;old.imbue(std::locale::classic());old<<std::setprecision(17)<<'{';
    for(int f=0;f<12;f++){if(f)old<<',';old<<'"'<<f<<"\":"<<(4000.123456789+i*.001+f);}
    old<<'}';old_bytes+=old.str().size();
  }
  auto middle=std::chrono::steady_clock::now();
  for(int i=0;i<count;i++){
    NativeJson fresh;for(int f=0;f<12;f++)fresh.num(std::to_string(f).c_str(),4000.123456789+i*.001+f);
    new_bytes+=fresh.done().size();
  }
  auto end=std::chrono::steady_clock::now();
  assert(old_bytes>0&&new_bytes>0);
  std::cout<<"JSON benchmark records="<<count<<" stream_ms="<<std::chrono::duration_cast<std::chrono::milliseconds>(middle-start).count()
           <<" chars_ms="<<std::chrono::duration_cast<std::chrono::milliseconds>(end-middle).count()<<'\n';
}
