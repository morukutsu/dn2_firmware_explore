| OS 1.11, phase zero of a master sequencer step. Keep a stock deadline
| if already armed. Finite RESET always arms its own boundary earlier in
| the preceding step. With RESET INF stock only does so if a cue is already
| visible then. Admit a Program Change that arrived in the intervening phase.
    tst.l   0x42c5aaac
    bne.w   activate

    move.l  %d0,-(%sp)
    move.l  %d1,-(%sp)
    move.l  0x42c5aac0,%d0
    cmpi.l  #0x4028c1e8,%d0     | stock single-pattern playback provider
    bne.w   skip
    cmp.l   0x42c5aac4,%d0      | no pending provider switch
    bne.w   skip
    cmpi.l  #1,%d2              | stock effective RESET, after override
    bne.w   skip
    move.l  #0x1dd54,%d1
    mvs.w   (%a5,%d1.l),%d0
    cmpi.l  #1,%d0              | the active pattern's stored RESET is INF
    bne.w   skip
    move.l  0x42c4e914,%d0
    cmpi.l  #1,%d0       | require a single pending descriptor
    bne.w   skip
    tst.l   0x42c4e910          | negative while the first cue is pending
    bpl.s   skip
    mvs.b   0x42c4e92c,%d0     | descriptor +20, -2 for incoming MIDI PC
    cmpi.l  #-2,%d0
    bne.w   skip
    move.l  0x42c4e918,%d0
    cmp.l   0x42c5a9a0,%d0      | skip a cue of the currently active pattern
    beq.s   skip

    move.l  #0x1dd56,%d1
    mvs.w   (%a5,%d1.l),%d1   | finite CHANGE length
    cmpi.l  #1,%d1
    ble.s   skip
    mvz.w   0x42c5a91c,%d0     | stock step index after increment
    remu.l  %d1,%d1,%d0
    tst.l   %d1
    bne.w   skip

    move.l  (%sp)+,%d1
    move.l  (%sp)+,%d0
activate:
    jmp     0x400da58e         | stock resolves and activates the current cue
skip:
    move.l  (%sp)+,%d1
    move.l  (%sp)+,%d0
    jmp     0x400da8ea         | no transition at this step
