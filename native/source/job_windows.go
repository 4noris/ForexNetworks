//go:build windows

package main

import (
	"fmt"
	"syscall"
	"unsafe"
)

type basicLimit struct {
	PerProcessUserTime int64
	PerJobUserTime     int64
	Flags              uint32
	MinimumWorkingSet  uintptr
	MaximumWorkingSet  uintptr
	ActiveProcessLimit uint32
	Affinity           uintptr
	PriorityClass      uint32
	SchedulingClass    uint32
}
type ioCounters struct {
	ReadOperations  uint64
	WriteOperations uint64
	OtherOperations uint64
	ReadTransfer    uint64
	WriteTransfer   uint64
	OtherTransfer   uint64
}
type extendedLimit struct {
	Basic                 basicLimit
	IO                    ioCounters
	ProcessMemoryLimit    uintptr
	JobMemoryLimit        uintptr
	PeakProcessMemoryUsed uintptr
	PeakJobMemoryUsed     uintptr
}

func ownProcessTree() (func(), error) {
	kernel := syscall.NewLazyDLL("kernel32.dll")
	job, _, err := kernel.NewProc("CreateJobObjectW").Call(0, 0)
	if job == 0 {
		return nil, fmt.Errorf("create child-process job: %v", err)
	}
	limits := extendedLimit{}
	limits.Basic.Flags = 0x2000
	ok, _, err := kernel.NewProc("SetInformationJobObject").Call(job, 9, uintptr(unsafe.Pointer(&limits)), unsafe.Sizeof(limits))
	if ok == 0 {
		syscall.CloseHandle(syscall.Handle(job))
		return nil, fmt.Errorf("set child-process job: %v", err)
	}
	process, _, _ := kernel.NewProc("GetCurrentProcess").Call()
	ok, _, err = kernel.NewProc("AssignProcessToJobObject").Call(job, process)
	if ok == 0 {
		syscall.CloseHandle(syscall.Handle(job))
		return nil, fmt.Errorf("own child-process tree: %v", err)
	}
	return func() { _ = syscall.CloseHandle(syscall.Handle(job)) }, nil
}
