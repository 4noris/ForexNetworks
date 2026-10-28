// Go owns the native read cache and gateway. Financial records arrive only
// from the C++ MT5 collector; there is no CSV/demo data path.
package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"runtime/debug"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

type Event struct {
	Kind            string          `json:"kind"`
	Op              string          `json:"op"`
	ID              string          `json:"id"`
	Data            json.RawMessage `json:"data"`
	Received        int64           `json:"received_ms"`
	SnapshotStarted int64           `json:"snapshot_started_ms,omitempty"`
	Sequence        uint64          `json:"sequence"`
	Epoch           string          `json:"epoch"`
}
type subscriber struct {
	events chan Event
	done   chan struct{}
}
type Book struct {
	changeHub changeHub
	sync.RWMutex
	Records          map[string]map[string]json.RawMessage `json:"records"`
	Sequence         uint64                                `json:"sequence"`
	Epoch            string                                `json:"epoch"`
	Complete         bool                                  `json:"complete"`
	Connected        bool                                  `json:"connected"`
	AccountMS        int64                                 `json:"account_ms"`
	PositionMS       int64                                 `json:"position_ms"`
	LastMS           int64                                 `json:"last_event_ms"`
	Counts           map[string]uint64                     `json:"event_counts"`
	ring             []Event
	ringBytes        int
	subscribers      map[*subscriber]bool
	latencies        []int64
	positionChanges  map[string]int64
	bootstrapStarted int64
	bootstrapReady   int64
	bootstrapEnded   bool
	versions         map[string]map[string]uint64
	changeFloor      uint64
	deleted          []deletedVersion
	ServerTimeMS     int64
}

func token() string {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		panic(err)
	}
	return hex.EncodeToString(b)
}
func newBook() *Book {
	return &Book{Records: make(map[string]map[string]json.RawMessage), Counts: make(map[string]uint64), Epoch: token(), subscribers: make(map[*subscriber]bool), positionChanges: make(map[string]int64)}
}
func recordID(kind string, data json.RawMessage) (string, error) {
	var row map[string]json.RawMessage
	if err := json.Unmarshal(data, &row); err != nil {
		return "", err
	}
	key := "Symbol"
	if kind == "users" || kind == "accounts" {
		key = "Login"
	}
	if kind == "positions" {
		key = "Ticket"
	}
	if kind == "groups" {
		key = "Group"
	}
	v, ok := row[key]
	if !ok {
		return "", fmt.Errorf("record missing %s", key)
	}
	return strings.Trim(string(v), "\""), nil
}
func (b *Book) apply(e Event) error {
	b.Lock()
	defer b.Unlock()
	var changed [][2]string
	if e.Op == "reset" {
		b.Records = make(map[string]map[string]json.RawMessage)
		b.versions = make(map[string]map[string]uint64)
		b.changeFloor = 0
		b.deleted = nil
		b.ServerTimeMS = 0
		b.Complete = false
		b.Connected = false
		b.AccountMS = 0
		b.PositionMS = 0
		b.Epoch = token()
		b.positionChanges = make(map[string]int64)
		b.bootstrapStarted = e.Received
		b.bootstrapReady = 0
		b.bootstrapEnded = false
		b.latencies = nil
		b.ring = nil
		b.ringBytes = 0
	}
	switch e.Op {
	case "upsert":
		if _, err := recordID(e.Kind, e.Data); err != nil {
			return err
		}
		if b.Records[e.Kind] == nil {
			b.Records[e.Kind] = make(map[string]json.RawMessage)
		}
		if !bytes.Equal(b.Records[e.Kind][e.ID], e.Data) {
			changed = append(changed, [2]string{e.Kind, e.ID})
		}
		b.Records[e.Kind][e.ID] = append(json.RawMessage(nil), e.Data...)
		if e.Kind == "positions" {
			b.positionChanges[e.ID] = e.Received
		}
		if e.Kind == "deals" && len(b.Records[e.Kind]) > 4096 {
			for id := range b.Records[e.Kind] {
				if id != e.ID {
					changed = append(changed, [2]string{e.Kind, id})
				}
			}
			b.Records[e.Kind] = map[string]json.RawMessage{e.ID: e.Data}
		}
	case "delete":
		changed = append(changed, [2]string{e.Kind, e.ID})
		delete(b.Records[e.Kind], e.ID)
		if e.Kind == "positions" {
			b.positionChanges[e.ID] = e.Received
		}
		if e.Kind == "users" {
			delete(b.Records["accounts"], e.ID)
			changed = append(changed, [2]string{"accounts", e.ID})
		}
	case "clean_login":
		for id, row := range b.Records[e.Kind] {
			var v struct {
				Login json.Number `json:"Login"`
			}
			if json.Unmarshal(row, &v) == nil && v.Login.String() == e.ID {
				delete(b.Records[e.Kind], id)
				changed = append(changed, [2]string{e.Kind, id})
				if e.Kind == "positions" {
					b.positionChanges[id] = e.Received
				}
			}
		}
	case "replace":
		var rows []json.RawMessage
		if err := json.Unmarshal(e.Data, &rows); err != nil {
			return err
		}
		replacement := make(map[string]json.RawMessage, len(rows))
		for _, row := range rows {
			id, err := recordID(e.Kind, row)
			if err != nil {
				return err
			}
			if _, dup := replacement[id]; dup {
				return errors.New("duplicate record in authoritative snapshot")
			}
			replacement[id] = row
		}
		previous := b.Records[e.Kind]
		b.Records[e.Kind] = replacement
		if e.Kind == "positions" {
			if e.SnapshotStarted > 0 {
				for id, changed := range b.positionChanges {
					if changed >= e.SnapshotStarted {
						if current, exists := previous[id]; exists {
							replacement[id] = current
						} else {
							delete(replacement, id)
						}
					} else {
						delete(b.positionChanges, id)
					}
				}
				ids := make([]string, 0, len(replacement))
				for id := range replacement {
					ids = append(ids, id)
				}
				sort.Strings(ids)
				rows = rows[:0]
				for _, id := range ids {
					rows = append(rows, replacement[id])
				}
				e.Data, _ = json.Marshal(rows)
			}
			b.PositionMS = e.Received
		}
		for id, row := range replacement {
			if !bytes.Equal(previous[id], row) {
				changed = append(changed, [2]string{e.Kind, id})
			}
		}
		for id := range previous {
			if _, exists := replacement[id]; !exists {
				changed = append(changed, [2]string{e.Kind, id})
			}
		}
	case "cycle_end":
		if e.Kind == "accounts" {
			b.AccountMS = e.Received
		}
	case "snapshot_end":
		// User callbacks can arrive while a large account snapshot is in flight.
		// Keep consuming the queued account reads instead of restarting bootstrap.
		b.bootstrapEnded = true
		b.Connected = true
	case "heartbeat", "status":
		var status struct {
			Connected    bool  `json:"connected"`
			ServerTimeMS int64 `json:"server_time_msc"`
		}
		if err := json.Unmarshal(e.Data, &status); err != nil {
			return err
		}
		b.Connected = status.Connected
		if status.ServerTimeMS > 0 {
			b.ServerTimeMS = status.ServerTimeMS
		}
	case "reset":
	default:
		return fmt.Errorf("unsupported native operation %q", e.Op)
	}
	if b.bootstrapEnded && !b.Complete && (e.Op == "snapshot_end" ||
		(e.Kind == "accounts" && e.Op == "cycle_end") || (e.Kind == "users" && e.Op == "delete")) {
		covered := true
		for id := range b.Records["users"] {
			if _, ok := b.Records["accounts"][id]; !ok {
				covered = false
				break
			}
		}
		if covered {
			b.Complete = true
			b.bootstrapReady = time.Now().UnixMilli()
			b.latencies = nil
		}
	}
	b.Sequence++
	for _, key := range changed {
		b.markChanged(key[0], key[1], b.Sequence)
	}
	e.Sequence = b.Sequence
	e.Epoch = b.Epoch
	b.LastMS = time.Now().UnixMilli()
	b.Counts[e.Kind]++
	if e.Received > 0 && b.bootstrapReady > 0 && e.Received >= b.bootstrapReady && e.Op != "snapshot_end" {
		latency := b.LastMS - e.Received
		if latency >= 0 {
			b.latencies = append(b.latencies, latency)
			if len(b.latencies) > 2048 {
				b.latencies = b.latencies[1024:]
			}
		}
	}
	b.retainEvent(e)
	for s := range b.subscribers {
		select {
		case s.events <- e:
		default:
			log.Print("native stream consumer exceeded bounded backlog; full snapshot required")
			close(s.done)
			delete(b.subscribers, s)
		}
	}
	return nil
}
func (b *Book) disconnect() {
	_ = b.apply(Event{Kind: "status", Op: "status", Data: json.RawMessage(`{"connected":false}`), Received: time.Now().UnixMilli()})
}
func (b *Book) health() map[string]any {
	b.RLock()
	defer b.RUnlock()
	now := time.Now().UnixMilli()
	ages := map[string]any{}
	for key, value := range map[string]int64{"account_age_ms": b.AccountMS, "position_age_ms": b.PositionMS, "transport_age_ms": b.LastMS} {
		if value > 0 {
			ages[key] = now - value
		} else {
			ages[key] = nil
		}
	}
	latencies := append([]int64(nil), b.latencies...)
	sort.Slice(latencies, func(i, j int) bool { return latencies[i] < latencies[j] })
	var p50, p95 any
	if len(latencies) > 0 {
		p50 = latencies[(len(latencies)-1)/2]
		p95 = latencies[(len(latencies)-1)*95/100]
	}
	ready := b.Complete && b.Connected && b.AccountMS > 0 && b.PositionMS > 0 && (now-b.AccountMS < 10000 || len(b.Records["positions"]) == 0) && now-b.PositionMS < 10000 && now-b.LastMS < 10000
	counts := map[string]int{}
	for kind, rows := range b.Records {
		counts[kind] = len(rows)
	}
	return map[string]any{"ready": ready, "connected": b.Connected, "complete": b.Complete, "source": "mt5-cpp-native", "epoch": b.Epoch, "sequence": b.Sequence, "ages": ages, "record_counts": counts, "latency_scope": "post_sync", "latency_samples": len(latencies), "collector_to_gateway_p50_ms": p50, "collector_to_gateway_p95_ms": p95}
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func secure(secret string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		host, _, _ := net.SplitHostPort(r.RemoteAddr)
		ip := net.ParseIP(host)
		actual := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if ip == nil || !ip.IsLoopback() || subtle.ConstantTimeCompare([]byte(actual), []byte(secret)) != 1 {
			http.Error(w, "Forbidden", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}
func (b *Book) snapshot(w http.ResponseWriter, r *http.Request) {
	// Capture immutable row references, then release the producer before JSON
	// encoding/network I/O on a large broker snapshot.
	b.RLock()
	records := make(map[string]map[string]json.RawMessage, len(b.Records))
	for kind, rows := range b.Records {
		copied := make(map[string]json.RawMessage, len(rows))
		for id, row := range rows {
			copied[id] = row
		}
		records[kind] = copied
	}
	value := struct {
		Records    map[string]map[string]json.RawMessage `json:"records"`
		Sequence   uint64                                `json:"sequence"`
		Epoch      string                                `json:"epoch"`
		Complete   bool                                  `json:"complete"`
		Connected  bool                                  `json:"connected"`
		AccountMS  int64                                 `json:"account_ms"`
		PositionMS int64                                 `json:"position_ms"`
	}{records, b.Sequence, b.Epoch, b.Complete, b.Connected, b.AccountMS, b.PositionMS}
	b.RUnlock()
	writeJSON(w, 200, value)
}
func (b *Book) events(w http.ResponseWriter, r *http.Request) {
	after, err := strconv.ParseUint(r.URL.Query().Get("after"), 10, 64)
	if err != nil {
		http.Error(w, "Invalid sequence", 400)
		return
	}
	// A single live account cycle can contain thousands of verified records.
	// Absorb that burst while the consumer publishes its preceding generation.
	// Keep a hard bound and force a full resync if the consumer cannot catch up.
	s := &subscriber{events: make(chan Event, 32768), done: make(chan struct{})}
	b.Lock()
	if r.URL.Query().Get("epoch") != b.Epoch || (len(b.ring) > 0 && after+1 < b.ring[0].Sequence) {
		b.Unlock()
		http.Error(w, "Snapshot required", 409)
		return
	}
	backlog := make([]Event, 0)
	for _, e := range b.ring {
		if e.Sequence > after {
			backlog = append(backlog, e)
		}
	}
	b.subscribers[s] = true
	b.Unlock()
	defer func() { b.Lock(); delete(b.subscribers, s); b.Unlock() }()
	w.Header().Set("Content-Type", "application/x-ndjson")
	w.Header().Set("Cache-Control", "no-store")
	controller := http.NewResponseController(w)
	encoder := json.NewEncoder(w)
	send := func(e Event) bool {
		_ = controller.SetWriteDeadline(time.Now().Add(10 * time.Second))
		if err := encoder.Encode(e); err != nil {
			return false
		}
		return controller.Flush() == nil
	}
	for _, e := range backlog {
		if !send(e) {
			return
		}
	}
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case e := <-s.events:
			if !send(e) {
				return
			}
		case <-ticker.C:
			if !send(Event{Kind: "transport", Op: "keepalive", Received: time.Now().UnixMilli()}) {
				return
			}
		case <-s.done:
			return
		case <-r.Context().Done():
			return
		}
	}
}
func loadEnv(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	scanner := bufio.NewScanner(f)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		value = strings.TrimSpace(value)
		if len(value) >= 2 && ((value[0] == '"' && value[len(value)-1] == '"') || (value[0] == '\'' && value[len(value)-1] == '\'')) {
			value = value[1 : len(value)-1]
		}
		if _, exists := os.LookupEnv(key); !exists {
			if err := os.Setenv(key, value); err != nil {
				return err
			}
		}
	}
	return scanner.Err()
}
func setEnv(env []string, key, value string) []string {
	prefix := key + "="
	out := make([]string, 0, len(env)+1)
	for _, item := range env {
		if !strings.HasPrefix(item, prefix) {
			out = append(out, item)
		}
	}
	return append(out, prefix+value)
}
func superviseCollector(ctx context.Context, b *Book, path, dir string) {
	for ctx.Err() == nil {
		command := exec.CommandContext(ctx, path)
		command.Dir = dir
		command.Env = os.Environ()
		command.Stderr = os.Stderr
		pipe, err := command.StdoutPipe()
		if err == nil {
			err = command.Start()
		}
		if err == nil {
			decoder := json.NewDecoder(bufio.NewReaderSize(pipe, 1024*1024))
			for {
				var event Event
				if err = decoder.Decode(&event); err != nil {
					break
				}
				if err = b.apply(event); err != nil {
					log.Printf("native snapshot rejected: %v", err)
					break
				}
			}
			_ = command.Process.Kill()
			_ = command.Wait()
		}
		b.disconnect()
		if ctx.Err() != nil {
			return
		}
		log.Print("collector disconnected; restarting with full source resynchronization")
		select {
		case <-ctx.Done():
			return
		case <-time.After(3 * time.Second):
		}
	}
}
func main() {
	if os.Getenv("GOMEMLIMIT") == "" {
		// Leave headroom for the licensed SDK cache, Python and the UI. This is
		// a soft GC budget; callers can explicitly select a different budget.
		debug.SetMemoryLimit(384 * 1024 * 1024)
	}
	root := flag.String("root", ".", "RMS root")
	listen := flag.String("listen", "127.0.0.1:8000", "Gateway listener")
	backendPort := flag.String("backend-port", "8001", "Internal analytics listener")
	collector := flag.String("collector", "", "Native collector executable")
	python := flag.String("python", "", "Python reporting interpreter")
	noBackend := flag.Bool("no-backend", false, "Run the read-only collector and cache without starting analytics")
	runtimeDir := flag.String("runtime-dir", "", "SDK cache directory")
	sdk := flag.String("sdk-libs", `D:\MetaTrader5SDK\Libs`, "Installed licensed SDK libraries")
	flag.Parse()
	closeJob, err := ownProcessTree()
	if err != nil {
		log.Fatal(err)
	}
	defer closeJob()
	absolute, err := filepath.Abs(*root)
	if err != nil {
		log.Fatal(err)
	}
	*root = absolute
	if err = loadEnv(filepath.Join(*root, ".env")); err != nil {
		log.Fatal("Cannot read RMS configuration")
	}
	if *collector == "" {
		*collector = filepath.Join(*root, "native", "bin", "mt5-collector.exe")
	}
	if *python == "" {
		*python = filepath.Join(*root, ".venv", "Scripts", "python.exe")
	}
	secret := os.Getenv("RMS_NATIVE_TOKEN")
	if secret == "" {
		secret = token()
	}
	_ = os.Setenv("RMS_NATIVE_TOKEN", secret)
	_ = os.Setenv("MT5_SDK_LIB_DIR", *sdk)
	nativeDir := filepath.Join(*root, "native", "runtime")
	if *runtimeDir != "" {
		nativeDir = *runtimeDir
	}
	if err = os.MkdirAll(nativeDir, 0700); err != nil {
		log.Fatal(err)
	}
	_ = os.Setenv("RMS_NATIVE_DATA_DIR", nativeDir)
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt)
	defer cancel()
	book := newBook()
	target, _ := url.Parse("http://127.0.0.1:" + *backendPort)
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		writeJSON(w, 503, map[string]string{"detail": "RMS analytics is starting or unavailable"})
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/internal/native/snapshot", secure(secret, book.snapshot))
	mux.HandleFunc("/internal/native/events", secure(secret, book.events))
	mux.HandleFunc("/internal/native/changes", secure(secret, book.changes))
	mux.HandleFunc("/internal/native/ack", secure(secret, book.acknowledgeChanges))
	mux.HandleFunc("/internal/native/health", secure(secret, func(w http.ResponseWriter, r *http.Request) { writeJSON(w, 200, book.health()) }))
	mux.Handle("/", proxy)
	listener, err := net.Listen("tcp", *listen)
	if err != nil {
		log.Fatal(err)
	}
	server := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 90 * time.Second, MaxHeaderBytes: 64 * 1024}
	go func() {
		if err := server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("gateway stopped: %v", err)
			cancel()
		}
	}()
	_, port, _ := net.SplitHostPort(listener.Addr().String())
	backend := exec.CommandContext(ctx, *python, "api.py")
	backend.Dir = *root
	env := setEnv(os.Environ(), "PORT", *backendPort)
	env = setEnv(env, "RMS_NATIVE_URL", "http://127.0.0.1:"+port)
	env = setEnv(env, "RMS_NATIVE_TOKEN", secret)
	env = setEnv(env, "OPENBLAS_NUM_THREADS", "1")
	env = setEnv(env, "OMP_NUM_THREADS", "1")
	env = setEnv(env, "RMS_API_HOST", "127.0.0.1")
	backend.Env = env
	backend.Stdout = os.Stdout
	backend.Stderr = os.Stderr
	if !*noBackend {
		if err = backend.Start(); err != nil {
			log.Fatal("Cannot start RMS reporting backend")
		}
		go func() { _ = backend.Wait(); cancel() }()
	}
	go superviseCollector(ctx, book, *collector, nativeDir)
	log.Printf("RMS Go gateway listening on %s; native SDK collector starting", listener.Addr())
	<-ctx.Done()
	shutdown, done := context.WithTimeout(context.Background(), 5*time.Second)
	defer done()
	_ = server.Shutdown(shutdown)
	if backend.Process != nil {
		_ = backend.Process.Kill()
	}
}
