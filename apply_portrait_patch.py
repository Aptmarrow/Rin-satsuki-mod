#!/usr/bin/env python3
"""
apply_portrait_patch.py
Applies assembly patches and safe cave hooks for Rin Satsuki's portraits
(slpl02a / slpl02b) and weapon select screen (Wind Sign / Flower Sign) in Touhou 6: New Classic.
"""

import struct
import os

def apply_patches(target_exe_path):
    print(f'Patching {target_exe_path}...')
    base_exe_path = 'th06nc/th06nc.exe.pre_slpl'
    if not os.path.exists(base_exe_path):
        base_exe_path = 'th06nc.exe.pre_slpl'
    with open(base_exe_path, 'rb') as f:
        exe = bytearray(f.read())

    pe_off = struct.unpack('<I', exe[0x3c:0x40])[0]
    opt_hdr_size = struct.unpack('<H', exe[pe_off + 20 : pe_off + 22])[0]
    sec_table_off = pe_off + 24 + opt_hdr_size
    num_secs = struct.unpack('<H', exe[pe_off + 6 : pe_off + 8])[0]

    sections = []
    for i in range(num_secs):
        s = sec_table_off + i * 40
        name = exe[s:s+8].rstrip(bytes([0])).decode('latin1')
        vsize, rva, rsize, rptr, flags = struct.unpack('<IIIII', exe[s+8:s+28])
        sections.append({'name': name, 'rva': rva, 'vsize': vsize, 'rsize': rsize, 'rptr': rptr, 'hdr': s})

    def va_to_raw(va):
        rva = va - 0x140000000
        for sec in sections:
            if sec['rva'] <= rva < sec['rva'] + max(sec['vsize'], sec['rsize']):
                return rva - sec['rva'] + sec['rptr']
        raise ValueError(f'Invalid VA: {hex(va)}')

    def patch(va, code, desc=''):
        raw = va_to_raw(va)
        exe[raw : raw + len(code)] = code
        print(f'  [+] Patched {len(code):>2} bytes at VA {hex(va)} (raw {hex(raw)}): {desc}')

    # Update .rdata section header:
    # Expand VirtualSize to 0x63000 to cover all raw padding and enable EXECUTE
    rdata_sec = sections[1]
    exe[rdata_sec['hdr'] + 8 : rdata_sec['hdr'] + 12] = struct.pack('<I', 0x63000)
    old_flags = struct.unpack('<I', exe[rdata_sec['hdr'] + 36 : rdata_sec['hdr'] + 40])[0]
    exe[rdata_sec['hdr'] + 36 : rdata_sec['hdr'] + 40] = struct.pack('<I', old_flags | 0x60000020)
    print('  [+] Section .rdata expanded to 0x63000 and marked EXECUTABLE + CODE')

    VA_TEXT = 0x1402bfb80
    VA_RDATA = 0x1403226ba
    VA_ANMMGR_PTR = 0x140a6e9b0
    VA_ANM_LOAD = 0x1400020b0
    VA_ANM_INITVM = 0x140002760
    VA_CHAR_ID = 0x1404f1e80
    VA_WEAPON_TYPE = 0x1404f1e81

    # -------------------------------------------------------------
    # 1. TEXT Cave at 0x1402bfb80 (128 bytes available in .text)
    # -------------------------------------------------------------
    text_cave = bytearray()
    labels_t = {}
    relocs_t = []

    def Lt(name): labels_t[name] = len(text_cave)
    def Jt(op, label):
        off = len(text_cave)
        text_cave.extend(op + bytes([0]))
        relocs_t.append((off + len(op), label, 1, len(op) + 1))

    # Loader
    VA_LOADER = VA_TEXT
    Lt('loader')
    text_cave += bytes.fromhex('53')                                     # push rbx
    text_cave += bytes.fromhex('48 83 ec 20')                            # sub rsp, 0x20
    disp = VA_ANM_LOAD - (VA_TEXT + len(text_cave) + 5)
    text_cave += bytes.fromhex('e8') + struct.pack('<i', disp)           # call Anm_Load
    text_cave += bytes.fromhex('85 c0')                                  # test eax, eax
    Jt(bytes.fromhex('75'), 'load_done')
    text_cave += bytes.fromhex('31 db')                                  # xor ebx, ebx
    Lt('load_loop')
    text_cave += bytes.fromhex('8d 53 2a')                               # lea edx, [rbx + 0x2a]
    text_cave += bytes.fromhex('44 8d 8b 94 01 00 00')                   # lea r9d, [rbx + 0x194]
    tab_lea_off = len(text_cave)
    text_cave += bytes.fromhex('48 8d 05 00 00 00 00')                   # lea rax, [rip + off_table]
    text_cave += bytes.fromhex('0f b6 04 18')                            # movzx eax, byte ptr [rax + rbx]
    str_lea_off = len(text_cave)
    text_cave += bytes.fromhex('4c 8d 05 00 00 00 00')                   # lea r8, [rip + str_s02a]
    text_cave += bytes.fromhex('49 01 c0')                               # add r8, rax
    va_here = VA_TEXT + len(text_cave)
    disp_mgr = VA_ANMMGR_PTR - (va_here + 7)
    text_cave += bytes.fromhex('48 8b 0d') + struct.pack('<i', disp_mgr) # mov rcx, [rip + VA_ANMMGR_PTR]
    va_here = VA_TEXT + len(text_cave)
    disp_load = VA_ANM_LOAD - (va_here + 5)
    text_cave += bytes.fromhex('e8') + struct.pack('<i', disp_load)      # call Anm_Load
    text_cave += bytes.fromhex('85 c0')                                  # test eax, eax
    Jt(bytes.fromhex('75'), 'load_done')
    text_cave += bytes.fromhex('ff c3')                                  # inc ebx
    text_cave += bytes.fromhex('83 fb 03')                               # cmp ebx, 3
    Jt(bytes.fromhex('75'), 'load_loop')
    Lt('load_done')
    text_cave += bytes.fromhex('48 83 c4 20')                            # add rsp, 0x20
    text_cave += bytes.fromhex('5b')                                     # pop rbx
    text_cave += bytes.fromhex('c3')                                     # ret

    # Helper 1: GetWeaponVMOffset (calculates rcx = ((char_id & 1) * 2 + weapon_type) * 0x120)
    VA_HELPER1 = VA_TEXT + len(text_cave)
    Lt('helper1')
    disp_char1 = VA_CHAR_ID - (VA_TEXT + len(text_cave) + 7)
    text_cave += bytes.fromhex('0f b6 0d') + struct.pack('<i', disp_char1) # movzx ecx, byte ptr [char_id]
    text_cave += bytes.fromhex('83 e1 01')                                 # and ecx, 1
    disp_wpn1 = VA_WEAPON_TYPE - (VA_TEXT + len(text_cave) + 7)
    text_cave += bytes.fromhex('0f b6 05') + struct.pack('<i', disp_wpn1)  # movzx eax, byte ptr [weapon_type]
    text_cave += bytes.fromhex('8d 0c 48')                                 # lea ecx, [rax + rcx*2]
    text_cave += bytes.fromhex('48 8d 0c c9')                             # lea rcx, [rcx + rcx*8]
    text_cave += bytes.fromhex('48 c1 e1 05')                             # shl rcx, 5
    text_cave += bytes.fromhex('c3')                                         # ret

    # Helper 2: GetBlinkVMBase (returns 0x8b for Marisa, 0x89 for Reimu & Rin)
    VA_BLINK_HELPER = VA_TEXT + len(text_cave)
    Lt('helper2')
    disp_bchar = VA_CHAR_ID - (VA_TEXT + len(text_cave) + 7)
    text_cave += bytes.fromhex('0f b6 05') + struct.pack('<i', disp_bchar) # movzx eax, byte ptr [char_id]
    text_cave += bytes.fromhex('83 f8 01')                                 # cmp eax, 1
    text_cave += bytes.fromhex('b8 89 00 00 00')                         # mov eax, 0x89
    text_cave += bytes.fromhex('75 05')                                     # jne +5
    text_cave += bytes.fromhex('b8 8b 00 00 00')                         # mov eax, 0x8b
    text_cave += bytes.fromhex('c3')                                         # ret

    for off, label, size, ins_len in relocs_t:
        target = labels_t[label]
        disp = target - (off - (ins_len - size) + ins_len)
        struct.pack_into('b', text_cave, off, disp)

    assert len(text_cave) <= 128, f'TEXT cave size {len(text_cave)} exceeds 128 bytes!'
    print(f'  [+] TEXT cave size: {len(text_cave)} / 128 bytes')

    # -------------------------------------------------------------
    # 2. RDATA Cave at 0x1403226ba (326 bytes available in .rdata)
    # -------------------------------------------------------------
    rdata_cave = bytearray()
    labels_r = {}
    relocs_r = []

    def Lr(name): labels_r[name] = len(rdata_cave)
    def Jr(op, label):
        off = len(rdata_cave)
        rdata_cave.extend(op + bytes([0]))
        relocs_r.append((off + len(op), label, 1, len(op) + 1))
    def Jr4(op, label):
        off = len(rdata_cave)
        rdata_cave.extend(op + bytes([0, 0, 0, 0]))
        relocs_r.append((off + len(op), label, 4, len(op) + 4))

    # Strings:
    str_s02a = b'data/slpl02a.anm' + bytes([0])
    str_s02b = b'data/slpl02b.anm' + bytes([0])
    str_card = b'data/card02.anm' + bytes([0])
    off_s02a = 0
    off_s02b = len(str_s02a)
    off_card = off_s02b + len(str_s02b)

    str_data = bytearray(str_s02a + str_s02b + str_card)
    off_table_in_str = len(str_data)
    str_data += bytes([off_s02a, off_s02b, off_card, 0])

    rdata_cave += str_data

    # Fixup loader lea rip-relative offsets in text_cave:
    va_tab_ins = VA_TEXT + tab_lea_off
    disp_tab = (VA_RDATA + off_table_in_str) - (va_tab_ins + 7)
    struct.pack_into('<i', text_cave, tab_lea_off + 3, disp_tab)

    va_str_ins = VA_TEXT + str_lea_off
    disp_str = VA_RDATA - (va_str_ins + 7)
    struct.pack_into('<i', text_cave, str_lea_off + 3, disp_str)

    # Helper: InitVMPair (initializes [rdx] with r8d and [rdx+0x120] with r8d+1)
    VA_INIT_VM_PAIR = VA_RDATA + len(rdata_cave)
    Lr('init_vm_pair')
    rdata_cave += bytes.fromhex('53 55 48 83 ec 28')                     # push rbx; push rbp; sub rsp, 0x28
    rdata_cave += bytes.fromhex('48 89 d3')                              # mov rbx, rdx
    rdata_cave += bytes.fromhex('44 89 c5')                              # mov ebp, r8d (PRESERVE r8d across Anm_InitVM!)
    va_here = VA_RDATA + len(rdata_cave)
    disp = VA_ANMMGR_PTR - (va_here + 7)
    rdata_cave += bytes.fromhex('48 8b 0d') + struct.pack('<i', disp)    # mov rcx, [VA_ANMMGR_PTR]
    va_here = VA_RDATA + len(rdata_cave)
    disp_init = VA_ANM_INITVM - (va_here + 5)
    rdata_cave += bytes.fromhex('e8') + struct.pack('<i', disp_init)     # call Anm_InitVM
    rdata_cave += bytes.fromhex('48 8d 93 20 01 00 00')                  # lea rdx, [rbx + 0x120]
    rdata_cave += bytes.fromhex('44 8d 45 01')                           # lea r8d, [rbp + 1]
    va_here = VA_RDATA + len(rdata_cave)
    disp = VA_ANMMGR_PTR - (va_here + 7)
    rdata_cave += bytes.fromhex('48 8b 0d') + struct.pack('<i', disp)
    va_here = VA_RDATA + len(rdata_cave)
    disp_init = VA_ANM_INITVM - (va_here + 5)
    rdata_cave += bytes.fromhex('e8') + struct.pack('<i', disp_init)     # call Anm_InitVM
    rdata_cave += bytes.fromhex('48 83 c4 28 5d 5b c3')                  # add rsp, 0x28; pop rbp; pop rbx; ret

    # Function 1: UpdatePortrait (preserves interrupt in edi across Anm_InitVM calls!)
    VA_UPDATE_PORTRAIT = VA_RDATA + len(rdata_cave)
    Lr('update_portrait')
    rdata_cave += bytes.fromhex('66 85 d2')                              # test dx, dx
    Jr(bytes.fromhex('74'), 'up_done')
    rdata_cave += bytes.fromhex('57 53 48 83 ec 28')                     # push rdi; push rbx; sub rsp, 0x28
    rdata_cave += bytes.fromhex('89 d7')                                 # mov edi, edx (PRESERVE dx!)
    rdata_cave += bytes.fromhex('bb 0c 00 00 00')                        # mov ebx, 12
    rdata_cave += bytes.fromhex('8b 46 28')                              # mov eax, [rsi + 0x28] (char_id)
    rdata_cave += bytes.fromhex('83 f8 01')                              # cmp eax, 1
    Jr(bytes.fromhex('74'), 'up_swap')

    # Reimu or Rin
    rdata_cave += bytes.fromhex('41 b8 7d 01 00 00')                     # mov r8d, 0x17d
    rdata_cave += bytes.fromhex('83 f8 02')                              # cmp eax, 2
    Jr(bytes.fromhex('75'), 'up_check_curr')
    rdata_cave += bytes.fromhex('41 b8 94 01 00 00')                     # mov r8d, 0x194

    Lr('up_check_curr')
    rdata_cave += bytes.fromhex('66 44 39 86 18 23 01 00')               # cmp [rsi + 0x12318], r8w
    Jr(bytes.fromhex('74'), 'up_apply')
    rdata_cave += bytes.fromhex('48 8d 96 e0 22 01 00')                  # lea rdx, [rsi + 0x122e0] (VM 0)
    Jr4(bytes.fromhex('e8'), 'init_vm_pair')
    Jr(bytes.fromhex('eb'), 'up_apply')

    Lr('up_swap')
    rdata_cave += bytes.fromhex('87 fb')                                 # xchg edi, ebx

    Lr('up_apply')
    rdata_cave += bytes.fromhex('66 89 be 6a 23 01 00')                  # mov [rsi + 0x1236a], di (VM 0)
    rdata_cave += bytes.fromhex('66 89 be 8a 24 01 00')                  # mov [rsi + 0x1248a], di (VM 1)
    rdata_cave += bytes.fromhex('66 89 9e aa 25 01 00')                  # mov [rsi + 0x125aa], bx (VM 2)
    rdata_cave += bytes.fromhex('66 89 9e ca 26 01 00')                  # mov [rsi + 0x126ca], bx (VM 3)
    rdata_cave += bytes.fromhex('48 83 c4 28 5b 5f')                     # add rsp, 0x28; pop rbx; pop rdi
    Lr('up_done')
    rdata_cave += bytes.fromhex('c3')                                    # ret

    # Function 2: TransitionToWeaponSelect (switches VM 12 & VM 13 sprites directly, preserves scripts!)
    VA_TRANSITION = VA_RDATA + len(rdata_cave)
    Lr('transition')
    rdata_cave += bytes.fromhex('ba 15 00 00 00')                        # mov edx, 0x15
    Jr4(bytes.fromhex('e8'), 'update_portrait')                         # call UpdatePortrait (locks portrait)
    rdata_cave += bytes.fromhex('8a 46 28')                              # mov al, [rsi + 0x28] (char_id reload!)
    rdata_cave += bytes.fromhex('3c 01')                                 # cmp al, 1
    Jr(bytes.fromhex('74'), 'trans_exit')                               # je trans_exit (Marisa)
    rdata_cave += bytes.fromhex('41 b8 89 01 00 00')                     # mov r8d, 0x189 (Reimu cards)
    rdata_cave += bytes.fromhex('3c 02')                                 # cmp al, 2
    Jr(bytes.fromhex('75'), 'trans_set')
    rdata_cave += bytes.fromhex('41 b8 96 01 00 00')                     # mov r8d, 0x196 (Rin cards)
    Lr('trans_set')
    rdata_cave += bytes.fromhex('66 44 89 86 94 30 01 00')               # mov [rsi + 0x13094], r8w (VM 12 sprite)
    va_here = VA_RDATA + len(rdata_cave)
    disp_mgr = VA_ANMMGR_PTR - (va_here + 7)
    rdata_cave += bytes.fromhex('48 8b 05') + struct.pack('<i', disp_mgr)# mov rax, [VA_ANMMGR_PTR]
    rdata_cave += bytes.fromhex('4d 63 c8')                              # movsxd r9, r8d
    rdata_cave += bytes.fromhex('49 c1 e1 06')                           # shl r9, 6
    rdata_cave += bytes.fromhex('4a 8d 8c 08 08 06 00 00')               # lea rcx, [rax + r9 + 0x608]
    rdata_cave += bytes.fromhex('48 89 8e 60 31 01 00')                  # mov [rsi + 0x13160], rcx (VM 12 ptr)
    rdata_cave += bytes.fromhex('41 ff c0')                              # inc r8d (Type B)
    rdata_cave += bytes.fromhex('66 44 89 86 b4 31 01 00')               # mov [rsi + 0x131b4], r8w (VM 13 sprite)
    rdata_cave += bytes.fromhex('48 83 c1 40')                           # add rcx, 0x40
    rdata_cave += bytes.fromhex('48 89 8e 80 32 01 00')                  # mov [rsi + 0x13280], rcx (VM 13 ptr)
    Lr('trans_exit')
    rdata_cave += bytes.fromhex('ba 0d 00 00 00')                        # mov edx, 0xd
    rdata_cave += bytes.fromhex('c3')                                    # ret

    for off, label, size, ins_len in relocs_r:
        target = labels_r[label]
        disp = target - (off - (ins_len - size) + ins_len)
        if size == 1:
            struct.pack_into('b', rdata_cave, off, disp)
        else:
            struct.pack_into('<i', rdata_cave, off, disp)

    assert len(rdata_cave) <= 326, f'RDATA cave size {len(rdata_cave)} exceeds 326 bytes!'
    print(f'  [+] RDATA cave size: {len(rdata_cave)} / 326 bytes')

    patch(VA_TEXT, text_cave, f'Safe loader + helpers text cave at {hex(VA_TEXT)}')
    patch(VA_RDATA, rdata_cave, f'Strings + UpdatePortrait + Transition cave at {hex(VA_RDATA)}')

    # 1. Hook ANM loads
    disp_load_title = VA_LOADER - (0x14004abf9 + 5)
    patch(0x14004abf9, bytes.fromhex('e8') + struct.pack('<i', disp_load_title), 'Hook 0x14004abf9 (Title ANM load) -> VA_LOADER')

    disp_load_char = VA_LOADER - (0x14004c619 + 5)
    patch(0x14004c619, bytes.fromhex('e8') + struct.pack('<i', disp_load_char), 'Hook 0x14004c619 (Char Select ANM load) -> VA_LOADER')

    # 2. Hook Confirm Character at 0x14004b5ce -> call VA_TRANSITION
    disp_trans = VA_TRANSITION - (0x14004b5ce + 5)
    patch(0x14004b5ce, bytes.fromhex('e8') + struct.pack('<i', disp_trans), 'call TransitionToWeaponSelect at 0x14004b5ce')

    # 3. Hook Move Right at 0x14004b7b2
    right_hook = bytearray()
    right_hook += bytes.fromhex('ba 09 00 00 00')                         # mov edx, 9
    disp_call_r = VA_UPDATE_PORTRAIT - (0x14004b7b2 + 5 + 5)
    right_hook += bytes.fromhex('e8') + struct.pack('<i', disp_call_r)    # call UpdatePortrait
    disp_jmp_r = 0x140049ab6 - (0x14004b7b2 + 10 + 5)
    right_hook += bytes.fromhex('e9') + struct.pack('<i', disp_jmp_r)     # jmp 0x140049ab6
    right_hook += bytes.fromhex('90') * (72 - len(right_hook))
    patch(0x14004b7b2, right_hook, 'Move Right hook at 0x14004b7b2')

    # 4. Hook Move Left at 0x14004b8cb
    left_hook = bytearray()
    left_hook += bytes.fromhex('ba 0a 00 00 00')                          # mov edx, 10
    disp_call_l = VA_UPDATE_PORTRAIT - (0x14004b8cb + 5 + 5)
    left_hook += bytes.fromhex('e8') + struct.pack('<i', disp_call_l)     # call UpdatePortrait
    left_hook += bytes.fromhex('90') * (65 - len(left_hook))
    patch(0x14004b8cb, left_hook, 'Move Left hook at 0x14004b8cb')

    # 5. Character Select initial setup
    patch(0x14004b46e, bytes.fromhex('3c 01 75 17'), 'initial setup: cmp al, 1; jne 0x14004b489')
    disp_aa5 = 0x140049aa5 - (0x14004b480 + 5)
    patch(0x14004b480, bytes.fromhex('e9') + struct.pack('<i', disp_aa5) + bytes.fromhex('90 90 90 90'), 'initial setup: jmp 0x140049aa5')

    # 6. Character Select cancel setup
    patch(0x14004b57a, bytes.fromhex('ff ca 75 17'), 'cancel setup: dec edx; jnz 0x14004b595')
    disp_c404 = 0x14004c404 - (0x14004b58c + 2 + 6)
    patch(0x14004b58c, bytes.fromhex('85 d2 0f 84') + struct.pack('<i', disp_c404) + bytes.fromhex('90'), 'cancel setup: test edx, edx; je 0x14004c404')

    # 7. State 13 entry
    patch(0x14004b685, bytes.fromhex('3c 01 75 15'), 'State 13: cmp al, 1; jne 0x14004b69e')
    patch(0x14004b6b7, bytes.fromhex('3c 01 75 15'), 'State 13: cmp al, 1; jne 0x14004b6d0')

    # 8. State 14 entry
    disp_char14 = VA_CHAR_ID - (0x14004bb35 + 7)
    patch(0x14004bb35, bytes.fromhex('80 3d') + struct.pack('<i', disp_char14) + bytes.fromhex('01 75 05'), 'State 14: cmp [char_id], 1; jne 0x14004bb43')
    patch(0x14004bb7b, bytes.fromhex('3c 01 75 15'), 'State 14: cmp al, 1; jne 0x14004bb94')

    # 9. State 14 initial color
    disp_call1 = VA_HELPER1 - (0x14004bbf4 + 5)
    call1 = bytes.fromhex('e8') + struct.pack('<i', disp_call1) + bytes.fromhex('90') * (25 - 5)
    patch(0x14004bbf4, call1, 'State 14 initial color: call GetWeaponVMOffset')

    # 10. State 14 entry
    patch(0x14004bc65, bytes.fromhex('3c 01 75 15'), 'State 14 entry: cmp al, 1; jne 0x14004bc7e')
    patch(0x14004bc97, bytes.fromhex('3c 01 75 15'), 'State 14 entry: cmp al, 1; jne 0x14004bcb0')
    patch(0x14004bcb2, bytes.fromhex('74 0e'), 'State 14 entry: je 0x14004bcc2')
    disp_call2 = VA_HELPER1 - (0x14004bcc2 + 5)
    call2 = bytes.fromhex('e8') + struct.pack('<i', disp_call2) + bytes.fromhex('90') * (28 - 5)
    patch(0x14004bcc2, call2, 'State 14 entry: call GetWeaponVMOffset')

    # 11. State 14 navigation
    patch(0x14004c085, bytes.fromhex('3c 01 75 15'), 'State 14 nav: cmp al, 1; jne 0x14004c09e')
    patch(0x14004c0b7, bytes.fromhex('3c 01 75 15'), 'State 14 nav: cmp al, 1; jne 0x14004c0d0')
    patch(0x14004c0d2, bytes.fromhex('74 0e'), 'State 14 nav: je 0x14004c0e2')
    disp_call3 = VA_HELPER1 - (0x14004c0e2 + 5)
    call3 = bytes.fromhex('e8') + struct.pack('<i', disp_call3) + bytes.fromhex('90') * (28 - 5)
    patch(0x14004c0e2, call3, 'State 14 nav: call GetWeaponVMOffset')

    # 12. State 14 cancel & loop
    patch(0x14004c338, bytes.fromhex('3c 01 75 15'), 'State 14 cancel: cmp al, 1; jne 0x14004c351')
    patch(0x14004c36a, bytes.fromhex('3c 01 75 15'), 'State 14 loop: cmp al, 1; jne 0x14004c383')
    patch(0x14004c385, bytes.fromhex('74 0e'), 'State 14 loop: je 0x14004c395')
    disp_call4 = VA_HELPER1 - (0x14004c395 + 5)
    call4 = bytes.fromhex('e8') + struct.pack('<i', disp_call4) + bytes.fromhex('90') * (28 - 5)
    patch(0x14004c395, call4, 'State 14 loop: call GetWeaponVMOffset')

    # 13. Weapon Blinking
    disp_blink_hook = VA_BLINK_HELPER - (0x14004b992 + 5)
    blink_hook = bytes.fromhex('e8') + struct.pack('<i', disp_blink_hook) + bytes.fromhex('90') * (14 - 5)
    patch(0x14004b992, blink_hook, 'Hook 0x14004b992 to call GetBlinkVMBase')

    # 14. Transition to Game Mode Select
    z_code = bytearray()
    for off in [0x1236a, 0x1248a, 0x125aa, 0x126ca, 0x130ea, 0x1320a, 0x1332a, 0x1344a]:
        z_code += bytes.fromhex('66 41 89 91') + struct.pack('<I', off)
    z_code += bytes.fromhex('90') * (91 - len(z_code))
    patch(0x14004daf1, z_code, 'Zero portraits & weapons on Game Mode Select entry')

    with open(target_exe_path, 'wb') as f_out:
        f_out.write(exe)
    print(f'Successfully patched and saved {target_exe_path}!\n')

if __name__ == '__main__':
    apply_patches('th06nc/th06nc.exe')
    apply_patches('th06nc_rin_slot3.exe')
    if os.path.exists('th06nc.exe'):
        apply_patches('th06nc.exe')
