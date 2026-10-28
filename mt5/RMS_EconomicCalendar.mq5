// Attach to one chart in the connected MT5 terminal. Exports its built-in
// economic calendar to FILE_COMMON every five minutes for the RMS dashboard.
#property strict
#property version "1.00"

input int PastDays = 7;
input int FutureDays = 7;
input int RefreshMinutes = 5;

string Clean(string text)
  {
   StringReplace(text, "\t", " ");
   StringReplace(text, "\r", " ");
   StringReplace(text, "\n", " ");
   return text;
  }

string Importance(const ENUM_CALENDAR_EVENT_IMPORTANCE value)
  {
   if(value == CALENDAR_IMPORTANCE_HIGH) return "high";
   if(value == CALENDAR_IMPORTANCE_MODERATE) return "medium";
   return "low";
  }

string TimeMode(const ENUM_CALENDAR_EVENT_TIMEMODE value)
  {
   if(value == CALENDAR_TIMEMODE_DATETIME) return "timed";
   if(value == CALENDAR_TIMEMODE_DATE) return "all_day";
   if(value == CALENDAR_TIMEMODE_TENTATIVE) return "tentative";
   return "no_time";
  }

string NumberOrEmpty(const bool present, const double value, const int digits)
  {
   return present ? DoubleToString(value, MathMin(digits, 8)) : "";
  }

void ExportCalendar()
  {
   datetime server_now = TimeTradeServer();
   if(server_now <= 0) return;
   MqlCalendarValue values[];
   int count = CalendarValueHistory(values, server_now - PastDays * 86400,
                                   server_now + FutureDays * 86400);
   if(count < 0)
     {
      PrintFormat("RMS calendar request failed: %d", GetLastError());
      return;
     }

   int handle = FileOpen("RMS_EconomicCalendar.tsv",
                         FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_COMMON,
                         '\t', CP_UTF8);
   if(handle == INVALID_HANDLE)
     {
      PrintFormat("RMS calendar file open failed: %d", GetLastError());
      return;
     }
   FileWrite(handle, "utc_ts", "server_time", "currency", "title", "impact", "time_mode", "period",
             "actual", "forecast", "previous");
   long server_offset = (long)server_now - (long)TimeGMT();
   int written = 0;
   for(int i = 0; i < count; i++)
     {
      MqlCalendarEvent event;
      MqlCalendarCountry country;
      if(!CalendarEventById(values[i].event_id, event) ||
         !CalendarCountryById(event.country_id, country))
         continue;
      string currency = country.currency;
      StringToUpper(currency);
      if(StringLen(currency) != 3) continue;
      FileWrite(handle,
                (string)((long)values[i].time - server_offset),
                TimeToString(values[i].time, TIME_DATE | TIME_MINUTES),
                currency, Clean(event.name), Importance(event.importance), TimeMode(event.time_mode),
                TimeToString(values[i].period, TIME_DATE),
                NumberOrEmpty(values[i].HasActualValue(), values[i].GetActualValue(), (int)event.digits),
                NumberOrEmpty(values[i].HasForecastValue(), values[i].GetForecastValue(), (int)event.digits),
                NumberOrEmpty(values[i].HasPreviousValue(), values[i].GetPreviousValue(), (int)event.digits));
      written++;
     }
   FileClose(handle);
   PrintFormat("RMS calendar exported %d timed events", written);
  }

int OnInit()
  {
   EventSetTimer(MathMax(60, RefreshMinutes * 60));
   ExportCalendar();
   return INIT_SUCCEEDED;
  }

void OnTimer() { ExportCalendar(); }
void OnDeinit(const int reason) { EventKillTimer(); }
