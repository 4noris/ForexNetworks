#pragma once
#include <charconv>
#include <cmath>
#include <stdexcept>
#include <string>
#include <type_traits>

// Locale-independent round-trip JSON without a stream/locale allocation for
// every tick, position, account and envelope.
class NativeJson {
  std::string out;
  bool first=true;
  void quoted(const std::string& value){
    static const char* hex="0123456789abcdef";
    out+='"';
    for(unsigned char c:value){
      if(c=='"'||c=='\\'){out+='\\';out+=char(c);}
      else if(c<32){out+="\\u00";out+=hex[c>>4];out+=hex[c&15];}
      else out+=char(c);
    }
    out+='"';
  }
  void key(const char* name){if(!first)out+=',';first=false;quoted(name);out+=':';}
  template<class T>void number(T value){
    if constexpr(std::is_enum_v<T>){number(static_cast<std::underlying_type_t<T>>(value));}
    else if constexpr(std::is_same_v<T,bool>){out+=value?'1':'0';}
    else {
      char text[128];std::to_chars_result result;
      if constexpr(std::is_floating_point_v<T>){
        if(!std::isfinite(value)){out+="null";return;}
        result=std::to_chars(text,text+sizeof(text),value,std::chars_format::general,17);
      }else result=std::to_chars(text,text+sizeof(text),value);
      if(result.ec!=std::errc())throw std::runtime_error("Native JSON number conversion failed");
      out.append(text,result.ptr);
    }
  }
public:
  NativeJson(){out.reserve(512);out+='{';}
  void str8(const char* name,const std::string& value){key(name);quoted(value);}
  template<class T>void num(const char* name,T value){key(name);number(value);}
  void boolean(const char* name,bool value){key(name);out+=value?"true":"false";}
  void raw(const char* name,const std::string& value){key(name);out+=value;}
  std::string done(){out+='}';return std::move(out);}
};
