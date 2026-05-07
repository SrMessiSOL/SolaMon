//! Solamon on-chain save anchor program.
//!
//! This is the native Solana skeleton for the next phase. It anchors a player
//! save to a character NFT mint and stores only compact, durable state:
//! save hash, save URI, map/position, and party monster mints.

use solana_program::{
    account_info::{next_account_info, AccountInfo},
    declare_id,
    entrypoint,
    entrypoint::ProgramResult,
    instruction::{AccountMeta, Instruction},
    msg,
    program::{invoke, invoke_signed},
    program_error::ProgramError,
    pubkey,
    pubkey::Pubkey,
    rent::Rent,
    system_instruction,
    sysvar::Sysvar,
};

declare_id!("EvrG6acfhGmsDPK5gkwcbV5J5yR4Nqz4gGm3jG24Kq1d");

const PLAYER_SEED: &[u8] = b"player";
const SAVE_BLOB_SEED: &[u8] = b"save_blob";
const SAVE_BLOB_CHUNK_SEED: &[u8] = b"save_blob_chunk";
const SAVE_SLOT_BLOB_SEED: &[u8] = b"save_slot_blob";
const SAVE_COMPACT_SEED: &[u8] = b"save_compact";
const PLAYER_STATE_VERSION: u8 = 1;
const SAVE_BLOB_VERSION: u8 = 1;
const NAME_LEN: usize = 32;
const AVATAR_LEN: usize = 32;
const SAVE_URI_LEN: usize = 200;
const MAP_ID_LEN: usize = 32;
const MAX_PARTY: usize = 6;
const PUBKEY_LEN: usize = 32;
const TOKEN_ACCOUNT_MIN_LEN: usize = 72;
const TOKEN_ACCOUNT_MINT_OFFSET: usize = 0;
const TOKEN_ACCOUNT_OWNER_OFFSET: usize = 32;
const TOKEN_ACCOUNT_AMOUNT_OFFSET: usize = 64;
const SPL_TOKEN_TRANSFER_TAG: u8 = 3;
const SPL_TOKEN_PROGRAM_ID: Pubkey =
    pubkey!("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
const GAME_AUTHORITY: Pubkey =
    pubkey!("9S8GZ6gVYiBARWLCeWdoPqMdHGVHg9sMR3hYXzrTZtnY");
const MAX_SAVE_BLOB_CHUNK_BYTES: usize = 800;
const MAX_SAVE_SLOT_BLOB_BYTES: usize = 4096;
const MAX_SAVE_COMPACT_BYTES: usize = 2048;
const MAX_SAVE_COMPACT_WRITE_BYTES: usize = 900;
const GAME_ACTION_ID_LEN: usize = 32;

pub const SAVE_BLOB_MANIFEST_SPACE: usize = 1 // initialized
    + 1 // version
    + PUBKEY_LEN // owner
    + PUBKEY_LEN // character mint
    + PUBKEY_LEN // save hash
    + 1 // compression
    + 4 // total compressed bytes
    + 2 // chunk size
    + 2; // chunk count

pub const SAVE_BLOB_CHUNK_SPACE: usize = 1 // initialized
    + 1 // version
    + PUBKEY_LEN // owner
    + PUBKEY_LEN // character mint
    + PUBKEY_LEN // save hash
    + 2 // index
    + 2 // byte len
    + MAX_SAVE_BLOB_CHUNK_BYTES;

pub const SAVE_SLOT_BLOB_SPACE: usize = 1 // initialized
    + 1 // version
    + PUBKEY_LEN // owner
    + PUBKEY_LEN // character mint
    + PUBKEY_LEN // save hash
    + 1 // compression
    + 4 // total compressed bytes
    + 1 // game save slot
    + MAX_SAVE_SLOT_BLOB_BYTES;

pub const SAVE_COMPACT_SPACE: usize = 1 // initialized
    + 1 // version
    + PUBKEY_LEN // owner
    + PUBKEY_LEN // character mint
    + PUBKEY_LEN // save hash
    + PUBKEY_LEN // base save hash
    + 1 // compression
    + 1 // game save slot
    + 2 // byte len
    + MAX_SAVE_COMPACT_BYTES;

pub const PLAYER_STATE_SPACE: usize = 1 // initialized
    + 1 // version
    + PUBKEY_LEN // owner
    + PUBKEY_LEN // character mint
    + NAME_LEN
    + AVATAR_LEN
    + 8 // save version
    + PUBKEY_LEN // save hash
    + SAVE_URI_LEN
    + MAP_ID_LEN
    + 2 // tile x
    + 2 // tile y
    + 8 // saved at slot
    + 1 // party len
    + (MAX_PARTY * PUBKEY_LEN);

entrypoint!(process_instruction);

pub fn process_instruction(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    instruction_data: &[u8],
) -> ProgramResult {
    let (tag, rest) = instruction_data
        .split_first()
        .ok_or(ProgramError::InvalidInstructionData)?;

    match tag {
        0 => process_create_player(program_id, accounts, rest),
        1 => process_save_player_state(program_id, accounts, rest),
        2 => process_write_save_blob_manifest(program_id, accounts, rest),
        3 => process_write_save_blob_chunk(program_id, accounts, rest),
        4 => process_write_save_slot_blob_chunk(program_id, accounts, rest),
        5 => process_write_save_compact(program_id, accounts, rest),
        6 => process_spend_and_save_player_state(program_id, accounts, rest),
        7 => process_game_action_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::ChooseStarter,
        ),
        8 => process_game_action_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::GrantItems,
        ),
        9 => process_game_action_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::BattleResult,
        ),
        10 => process_game_action_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::CatchSolamon,
        ),
        11 => process_game_action_spend_and_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::BuyItem,
        ),
        12 => process_game_action_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::SellItem,
        ),
        13 => process_game_action_spend_and_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::HealParty,
        ),
        14 => process_game_action_save_player_state(
            program_id,
            accounts,
            rest,
            GameActionKind::ReleaseSolamon,
        ),
        _ => Err(ProgramError::InvalidInstructionData),
    }
}

fn process_game_action_save_player_state(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
    expected_action: GameActionKind,
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let action = parse_game_action(&mut cursor)?;
    validate_game_action(expected_action, &action, None)?;
    let update = parse_save_state_update(&mut cursor)?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let game_authority = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let player_state = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_game_authority(game_authority)?;
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;
    apply_save_state_update(
        program_id,
        owner,
        character_mint,
        player_state,
        update,
        Some(expected_action),
    )
}

fn process_game_action_spend_and_save_player_state(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
    expected_action: GameActionKind,
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let amount = cursor.read_u64()?;
    let currency_mint = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let action = parse_game_action(&mut cursor)?;
    validate_game_action(expected_action, &action, Some(amount))?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let game_authority = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let player_state = next_account_info(account_info_iter)?;
    let player_token_account = next_account_info(account_info_iter)?;
    let treasury_token_account = next_account_info(account_info_iter)?;
    let token_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_game_authority(game_authority)?;
    if token_program.key != &SPL_TOKEN_PROGRAM_ID {
        return Err(ProgramError::IncorrectProgramId);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;
    assert_token_account(
        player_token_account,
        &currency_mint,
        Some(owner.key),
        Some(amount),
    )?;
    assert_token_account(treasury_token_account, &currency_mint, None, None)?;

    if amount > 0 {
        let mut transfer_data = [0u8; 9];
        transfer_data[0] = SPL_TOKEN_TRANSFER_TAG;
        transfer_data[1..].copy_from_slice(&amount.to_le_bytes());
        let instruction = Instruction {
            program_id: SPL_TOKEN_PROGRAM_ID,
            accounts: vec![
                AccountMeta::new(*player_token_account.key, false),
                AccountMeta::new(*treasury_token_account.key, false),
                AccountMeta::new_readonly(*owner.key, true),
            ],
            data: transfer_data.to_vec(),
        };
        invoke(
            &instruction,
            &[
                player_token_account.clone(),
                treasury_token_account.clone(),
                owner.clone(),
                token_program.clone(),
            ],
        )?;
    }

    let update = parse_save_state_update(&mut cursor)?;
    apply_save_state_update(
        program_id,
        owner,
        character_mint,
        player_state,
        update,
        Some(expected_action),
    )
}

fn process_spend_and_save_player_state(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let _ = (program_id, accounts, data);
    msg!("Generic spend-and-save is disabled; use a typed game action instruction");
    return Err(ProgramError::InvalidInstructionData);
    #[allow(unreachable_code)]
    {
    let mut cursor = Cursor::new(data);
    let amount = cursor.read_u64()?;
    let currency_mint = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let _game_authority = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let player_state = next_account_info(account_info_iter)?;
    let player_token_account = next_account_info(account_info_iter)?;
    let treasury_token_account = next_account_info(account_info_iter)?;
    let token_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    if token_program.key != &SPL_TOKEN_PROGRAM_ID {
        return Err(ProgramError::IncorrectProgramId);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;
    assert_token_account(
        player_token_account,
        &currency_mint,
        Some(owner.key),
        Some(amount),
    )?;
    assert_token_account(treasury_token_account, &currency_mint, None, None)?;

    if amount > 0 {
        let mut transfer_data = [0u8; 9];
        transfer_data[0] = SPL_TOKEN_TRANSFER_TAG;
        transfer_data[1..].copy_from_slice(&amount.to_le_bytes());
        let instruction = Instruction {
            program_id: SPL_TOKEN_PROGRAM_ID,
            accounts: vec![
                AccountMeta::new(*player_token_account.key, false),
                AccountMeta::new(*treasury_token_account.key, false),
                AccountMeta::new_readonly(*owner.key, true),
            ],
            data: transfer_data.to_vec(),
        };
        invoke(
            &instruction,
            &[
                player_token_account.clone(),
                treasury_token_account.clone(),
                owner.clone(),
                token_program.clone(),
            ],
        )?;
    }

    let update = parse_save_state_update(&mut cursor)?;
    apply_save_state_update(program_id, owner, character_mint, player_state, update, None)
    }
}

fn process_write_save_compact(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let save_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let base_save_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let game_slot = cursor.read_u8()?;
    let compression = cursor.read_u8()?;
    let len = cursor.read_u16()? as usize;
    if len > MAX_SAVE_COMPACT_BYTES || len > MAX_SAVE_COMPACT_WRITE_BYTES {
        return Err(ProgramError::InvalidInstructionData);
    }
    let bytes = cursor.read_bytes(len)?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let rent_payer = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let compact = next_account_info(account_info_iter)?;
    let system_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    if !rent_payer.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;

    let slot_seed = [game_slot];
    let (expected_pda, bump) = Pubkey::find_program_address(
        &[
            SAVE_COMPACT_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            &slot_seed,
        ],
        program_id,
    );
    if expected_pda != *compact.key {
        return Err(ProgramError::InvalidSeeds);
    }

    create_pda_if_empty(
        rent_payer,
        compact,
        system_program,
        SAVE_COMPACT_SPACE,
        program_id,
        &[
            SAVE_COMPACT_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            &slot_seed,
            &[bump],
        ],
    )?;
    if compact.owner != program_id {
        return Err(ProgramError::IllegalOwner);
    }

    let mut account_data = compact.data.borrow_mut();
    if account_data.len() < SAVE_COMPACT_SPACE {
        return Err(ProgramError::AccountDataTooSmall);
    }
    let mut offset = 0;
    account_data[offset] = true as u8;
    offset += 1;
    account_data[offset] = SAVE_BLOB_VERSION;
    offset += 1;
    put_pubkey(&mut account_data, &mut offset, owner.key);
    put_pubkey(&mut account_data, &mut offset, character_mint.key);
    put_pubkey(&mut account_data, &mut offset, &save_hash);
    put_pubkey(&mut account_data, &mut offset, &base_save_hash);
    account_data[offset] = compression;
    offset += 1;
    account_data[offset] = game_slot;
    offset += 1;
    put_u16(&mut account_data, &mut offset, len as u16);
    account_data[offset..offset + MAX_SAVE_COMPACT_BYTES].fill(0);
    account_data[offset..offset + len].copy_from_slice(bytes);
    Ok(())
}

fn process_write_save_slot_blob_chunk(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let save_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let game_slot = cursor.read_u8()?;
    let compression = cursor.read_u8()?;
    let total_len = cursor.read_u32()? as usize;
    let offset = cursor.read_u16()? as usize;
    let len = cursor.read_u16()? as usize;
    if total_len > MAX_SAVE_SLOT_BLOB_BYTES
        || offset
            .checked_add(len)
            .ok_or(ProgramError::InvalidInstructionData)?
            > total_len
    {
        return Err(ProgramError::InvalidInstructionData);
    }
    let bytes = cursor.read_bytes(len)?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let rent_payer = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let save_blob = next_account_info(account_info_iter)?;
    let system_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    if !rent_payer.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;

    let slot_seed = [game_slot];
    let (expected_pda, bump) = Pubkey::find_program_address(
        &[
            SAVE_SLOT_BLOB_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            &slot_seed,
        ],
        program_id,
    );
    if expected_pda != *save_blob.key {
        return Err(ProgramError::InvalidSeeds);
    }

    create_pda_if_empty(
        rent_payer,
        save_blob,
        system_program,
        SAVE_SLOT_BLOB_SPACE,
        program_id,
        &[
            SAVE_SLOT_BLOB_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            &slot_seed,
            &[bump],
        ],
    )?;
    if save_blob.owner != program_id {
        return Err(ProgramError::IllegalOwner);
    }

    let mut account_data = save_blob.data.borrow_mut();
    if account_data.len() < SAVE_SLOT_BLOB_SPACE {
        return Err(ProgramError::AccountDataTooSmall);
    }
    let mut header_offset = 0;
    account_data[header_offset] = true as u8;
    header_offset += 1;
    account_data[header_offset] = SAVE_BLOB_VERSION;
    header_offset += 1;
    put_pubkey(&mut account_data, &mut header_offset, owner.key);
    put_pubkey(&mut account_data, &mut header_offset, character_mint.key);
    put_pubkey(&mut account_data, &mut header_offset, &save_hash);
    account_data[header_offset] = compression;
    header_offset += 1;
    put_u32(&mut account_data, &mut header_offset, total_len as u32);
    account_data[header_offset] = game_slot;
    header_offset += 1;

    let data_start = header_offset;
    account_data[data_start + offset..data_start + offset + len].copy_from_slice(bytes);
    Ok(())
}

fn process_write_save_blob_manifest(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let save_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let compression = cursor.read_u8()?;
    let total_len = cursor.read_u32()?;
    let chunk_size = cursor.read_u16()?;
    let chunk_count = cursor.read_u16()?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let rent_payer = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let manifest = next_account_info(account_info_iter)?;
    let system_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    if !rent_payer.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;

    let (expected_pda, bump) = Pubkey::find_program_address(
        &[
            SAVE_BLOB_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            save_hash.as_ref(),
        ],
        program_id,
    );
    if expected_pda != *manifest.key {
        return Err(ProgramError::InvalidSeeds);
    }

    create_pda_if_empty(
        rent_payer,
        manifest,
        system_program,
        SAVE_BLOB_MANIFEST_SPACE,
        program_id,
        &[
            SAVE_BLOB_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            save_hash.as_ref(),
            &[bump],
        ],
    )?;
    if manifest.owner != program_id {
        return Err(ProgramError::IllegalOwner);
    }

    let manifest_data = SaveBlobManifest {
        initialized: true,
        version: SAVE_BLOB_VERSION,
        owner: *owner.key,
        character_mint: *character_mint.key,
        save_hash,
        compression,
        total_len,
        chunk_size,
        chunk_count,
    };
    manifest_data.pack(&mut manifest.data.borrow_mut())?;
    Ok(())
}

fn process_write_save_blob_chunk(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let save_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let index = cursor.read_u16()?;
    let len = cursor.read_u16()? as usize;
    if len > MAX_SAVE_BLOB_CHUNK_BYTES {
        return Err(ProgramError::InvalidInstructionData);
    }
    let bytes = cursor.read_bytes(len)?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let rent_payer = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let chunk = next_account_info(account_info_iter)?;
    let system_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    if !rent_payer.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;

    let index_bytes = index.to_le_bytes();
    let (expected_pda, bump) = Pubkey::find_program_address(
        &[
            SAVE_BLOB_CHUNK_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            save_hash.as_ref(),
            &index_bytes,
        ],
        program_id,
    );
    if expected_pda != *chunk.key {
        return Err(ProgramError::InvalidSeeds);
    }

    create_pda_if_empty(
        rent_payer,
        chunk,
        system_program,
        SAVE_BLOB_CHUNK_SPACE,
        program_id,
        &[
            SAVE_BLOB_CHUNK_SEED,
            owner.key.as_ref(),
            character_mint.key.as_ref(),
            save_hash.as_ref(),
            &index_bytes,
            &[bump],
        ],
    )?;
    if chunk.owner != program_id {
        return Err(ProgramError::IllegalOwner);
    }

    let mut chunk_data = SaveBlobChunk {
        initialized: true,
        version: SAVE_BLOB_VERSION,
        owner: *owner.key,
        character_mint: *character_mint.key,
        save_hash,
        index,
        len: len as u16,
        bytes: [0; MAX_SAVE_BLOB_CHUNK_BYTES],
    };
    chunk_data.bytes[..len].copy_from_slice(bytes);
    chunk_data.pack(&mut chunk.data.borrow_mut())?;
    Ok(())
}

fn process_create_player(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let mut cursor = Cursor::new(data);
    let name = cursor.read_fixed_string(NAME_LEN)?;
    let avatar = cursor.read_fixed_string(AVATAR_LEN)?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let game_authority = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let player_state = next_account_info(account_info_iter)?;
    let system_program = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_game_authority(game_authority)?;
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;

    let (expected_pda, bump) = Pubkey::find_program_address(
        &[PLAYER_SEED, owner.key.as_ref(), character_mint.key.as_ref()],
        program_id,
    );
    if expected_pda != *player_state.key {
        msg!("Invalid player PDA");
        return Err(ProgramError::InvalidSeeds);
    }

    if player_state.data_is_empty() {
        let rent = Rent::get()?;
        let lamports = rent.minimum_balance(PLAYER_STATE_SPACE);
        invoke_signed(
            &system_instruction::create_account(
                owner.key,
                player_state.key,
                lamports,
                PLAYER_STATE_SPACE as u64,
                program_id,
            ),
            &[
                owner.clone(),
                player_state.clone(),
                system_program.clone(),
            ],
            &[&[
                PLAYER_SEED,
                owner.key.as_ref(),
                character_mint.key.as_ref(),
                &[bump],
            ]],
        )?;
    }

    if player_state.owner != program_id {
        return Err(ProgramError::IllegalOwner);
    }

    let mut state = PlayerState::default();
    state.initialized = true;
    state.version = PLAYER_STATE_VERSION;
    state.owner = *owner.key;
    state.character_mint = *character_mint.key;
    write_bytes(&mut state.name, name.as_bytes());
    write_bytes(&mut state.avatar, avatar.as_bytes());
    state.pack(&mut player_state.data.borrow_mut())?;

    Ok(())
}

fn process_save_player_state(
    program_id: &Pubkey,
    accounts: &[AccountInfo],
    data: &[u8],
) -> ProgramResult {
    let _ = (program_id, accounts, data);
    msg!("Generic save is disabled; use a typed game action instruction");
    return Err(ProgramError::InvalidInstructionData);
    #[allow(unreachable_code)]
    {
    let mut cursor = Cursor::new(data);
    let update = parse_save_state_update(&mut cursor)?;

    let account_info_iter = &mut accounts.iter();
    let owner = next_account_info(account_info_iter)?;
    let character_mint = next_account_info(account_info_iter)?;
    let character_token_account = next_account_info(account_info_iter)?;
    let player_state = next_account_info(account_info_iter)?;

    if !owner.is_signer {
        return Err(ProgramError::MissingRequiredSignature);
    }
    assert_nft_owner(character_token_account, character_mint.key, owner.key)?;
    apply_save_state_update(program_id, owner, character_mint, player_state, update, None)
    }
}

fn parse_save_state_update(cursor: &mut Cursor) -> Result<SaveStateUpdate, ProgramError> {
    let expected_previous_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let save_hash = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    let save_uri = cursor.read_fixed_string(SAVE_URI_LEN)?;
    let map_id = cursor.read_fixed_string(MAP_ID_LEN)?;
    let tile_x = cursor.read_u16()?;
    let tile_y = cursor.read_u16()?;
    let party_len = cursor.read_u8()? as usize;
    if party_len > MAX_PARTY {
        return Err(ProgramError::InvalidInstructionData);
    }

    let mut party = [Pubkey::default(); MAX_PARTY];
    for slot in party.iter_mut().take(party_len) {
        *slot = Pubkey::new_from_array(cursor.read_pubkey_bytes()?);
    }

    Ok(SaveStateUpdate {
        expected_previous_hash,
        save_hash,
        save_uri,
        map_id,
        tile_x,
        tile_y,
        party_len,
        party,
    })
}

fn apply_save_state_update(
    program_id: &Pubkey,
    owner: &AccountInfo,
    character_mint: &AccountInfo,
    player_state: &AccountInfo,
    update: SaveStateUpdate,
    action_kind: Option<GameActionKind>,
) -> ProgramResult {
    let clock = solana_program::clock::Clock::get()?;

    if player_state.owner != program_id {
        return Err(ProgramError::IllegalOwner);
    }

    let (expected_pda, _bump) = Pubkey::find_program_address(
        &[PLAYER_SEED, owner.key.as_ref(), character_mint.key.as_ref()],
        program_id,
    );
    if expected_pda != *player_state.key {
        return Err(ProgramError::InvalidSeeds);
    }

    let mut state = PlayerState::unpack(&player_state.data.borrow())?;
    if !state.initialized
        || state.owner != *owner.key
        || state.character_mint != *character_mint.key
    {
        return Err(ProgramError::InvalidAccountData);
    }
    if state.save_hash != update.expected_previous_hash {
        msg!("Save rejected: previous save hash does not match on-chain state");
        return Err(ProgramError::InvalidAccountData);
    }
    if let Some(kind) = action_kind {
        validate_party_transition(kind, &state, &update)?;
    }

    state.save_version = state
        .save_version
        .checked_add(1)
        .ok_or(ProgramError::InvalidInstructionData)?;
    state.save_hash = update.save_hash;
    write_bytes(&mut state.save_uri, update.save_uri.as_bytes());
    write_bytes(&mut state.map_id, update.map_id.as_bytes());
    state.tile_x = update.tile_x;
    state.tile_y = update.tile_y;
    state.saved_at_slot = clock.slot;
    state.party_len = update.party_len as u8;
    state.party = update.party;
    state.pack(&mut player_state.data.borrow_mut())?;

    Ok(())
}

fn validate_party_transition(
    action_kind: GameActionKind,
    state: &PlayerState,
    update: &SaveStateUpdate,
) -> ProgramResult {
    match action_kind {
        GameActionKind::ChooseStarter => {
            if state.party_len != 0 || update.party_len != 1 {
                msg!("Starter action must create the first party monster");
                return Err(ProgramError::InvalidInstructionData);
            }
        }
        GameActionKind::CatchSolamon => {
            let current_len = state.party_len as usize;
            if update.party_len != current_len && update.party_len != current_len.saturating_add(1) {
                msg!("Catch action has invalid party length change");
                return Err(ProgramError::InvalidInstructionData);
            }
            if !party_prefix_matches(state, update, current_len) {
                msg!("Catch action changed existing party members");
                return Err(ProgramError::InvalidInstructionData);
            }
        }
        GameActionKind::ReleaseSolamon => {
            if update.party_len > state.party_len as usize {
                msg!("Release action cannot increase party length");
                return Err(ProgramError::InvalidInstructionData);
            }
            if !party_is_subsequence(state, update) {
                msg!("Release action changed unrelated party members");
                return Err(ProgramError::InvalidInstructionData);
            }
        }
        GameActionKind::GrantItems
        | GameActionKind::BattleResult
        | GameActionKind::BuyItem
        | GameActionKind::SellItem
        | GameActionKind::HealParty => {
            if update.party_len != state.party_len as usize
                || !party_prefix_matches(state, update, state.party_len as usize)
            {
                msg!("Action cannot change party membership");
                return Err(ProgramError::InvalidInstructionData);
            }
        }
    }
    Ok(())
}

fn party_prefix_matches(
    state: &PlayerState,
    update: &SaveStateUpdate,
    len: usize,
) -> bool {
    if len > MAX_PARTY {
        return false;
    }
    for index in 0..len {
        if state.party[index] != update.party[index] {
            return false;
        }
    }
    true
}

fn party_is_subsequence(state: &PlayerState, update: &SaveStateUpdate) -> bool {
    let mut state_index = 0usize;
    let state_len = state.party_len as usize;
    for update_index in 0..update.party_len {
        let wanted = update.party[update_index];
        let mut found = false;
        while state_index < state_len {
            if state.party[state_index] == wanted {
                found = true;
                state_index += 1;
                break;
            }
            state_index += 1;
        }
        if !found {
            return false;
        }
    }
    true
}

struct SaveStateUpdate {
    expected_previous_hash: Pubkey,
    save_hash: Pubkey,
    save_uri: String,
    map_id: String,
    tile_x: u16,
    tile_y: u16,
    party_len: usize,
    party: [Pubkey; MAX_PARTY],
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum GameActionKind {
    ChooseStarter = 1,
    GrantItems = 2,
    BattleResult = 3,
    CatchSolamon = 4,
    BuyItem = 5,
    SellItem = 6,
    HealParty = 7,
    ReleaseSolamon = 8,
}

struct GameAction {
    kind: GameActionKind,
    amount: u16,
    aux_amount: u16,
    primary_id: String,
    secondary_id: String,
}

fn parse_game_action(cursor: &mut Cursor) -> Result<GameAction, ProgramError> {
    let kind = match cursor.read_u8()? {
        1 => GameActionKind::ChooseStarter,
        2 => GameActionKind::GrantItems,
        3 => GameActionKind::BattleResult,
        4 => GameActionKind::CatchSolamon,
        5 => GameActionKind::BuyItem,
        6 => GameActionKind::SellItem,
        7 => GameActionKind::HealParty,
        8 => GameActionKind::ReleaseSolamon,
        _ => return Err(ProgramError::InvalidInstructionData),
    };
    let amount = cursor.read_u16()?;
    let aux_amount = cursor.read_u16()?;
    let primary_id = cursor.read_fixed_string(GAME_ACTION_ID_LEN)?;
    let secondary_id = cursor.read_fixed_string(GAME_ACTION_ID_LEN)?;
    Ok(GameAction {
        kind,
        amount,
        aux_amount,
        primary_id,
        secondary_id,
    })
}

fn validate_game_action(
    expected: GameActionKind,
    action: &GameAction,
    token_spend: Option<u64>,
) -> ProgramResult {
    let _secondary_id = &action.secondary_id;
    if action.kind != expected {
        return Err(ProgramError::InvalidInstructionData);
    }
    match action.kind {
        GameActionKind::ChooseStarter => {
            require_id(&action.primary_id)?;
            require_amount_between(action.amount, 1, 1)?;
        }
        GameActionKind::GrantItems => {
            require_id(&action.primary_id)?;
            require_amount_between(action.amount, 1, 99)?;
        }
        GameActionKind::BattleResult => {
            require_id(&action.primary_id)?;
            if action.amount > 9999 || action.aux_amount > 9999 {
                return Err(ProgramError::InvalidInstructionData);
            }
        }
        GameActionKind::CatchSolamon => {
            require_id(&action.primary_id)?;
            require_amount_between(action.amount, 1, 1)?;
        }
        GameActionKind::BuyItem => {
            require_id(&action.primary_id)?;
            require_amount_between(action.amount, 1, 99)?;
            if token_spend.unwrap_or(0) == 0 {
                return Err(ProgramError::InvalidInstructionData);
            }
        }
        GameActionKind::SellItem => {
            require_id(&action.primary_id)?;
            require_amount_between(action.amount, 1, 99)?;
        }
        GameActionKind::HealParty => {
            if token_spend.unwrap_or(0) == 0 {
                return Err(ProgramError::InvalidInstructionData);
            }
        }
        GameActionKind::ReleaseSolamon => {
            require_id(&action.primary_id)?;
            require_amount_between(action.amount, 1, 1)?;
        }
    }
    Ok(())
}

fn require_id(value: &str) -> ProgramResult {
    if value.trim().is_empty() {
        return Err(ProgramError::InvalidInstructionData);
    }
    Ok(())
}

fn require_amount_between(value: u16, min: u16, max: u16) -> ProgramResult {
    if value < min || value > max {
        return Err(ProgramError::InvalidInstructionData);
    }
    Ok(())
}

fn assert_nft_owner(
    token_account_info: &AccountInfo,
    mint: &Pubkey,
    owner: &Pubkey,
) -> ProgramResult {
    if token_account_info.owner != &SPL_TOKEN_PROGRAM_ID {
        msg!("Character token account is not owned by the SPL Token program");
        return Err(ProgramError::IncorrectProgramId);
    }

    let data = token_account_info.data.borrow();
    if data.len() < TOKEN_ACCOUNT_MIN_LEN {
        return Err(ProgramError::InvalidAccountData);
    }
    let token_mint = Pubkey::new_from_array(get_array_at::<PUBKEY_LEN>(
        &data,
        TOKEN_ACCOUNT_MINT_OFFSET,
    ));
    let token_owner = Pubkey::new_from_array(get_array_at::<PUBKEY_LEN>(
        &data,
        TOKEN_ACCOUNT_OWNER_OFFSET,
    ));
    let amount = u64::from_le_bytes(get_array_at::<8>(
        &data,
        TOKEN_ACCOUNT_AMOUNT_OFFSET,
    ));
    if token_mint != *mint || token_owner != *owner || amount != 1 {
        msg!("Wallet does not hold exactly one character NFT token");
        return Err(ProgramError::InvalidAccountData);
    }

    Ok(())
}

fn assert_game_authority(game_authority: &AccountInfo) -> ProgramResult {
    if game_authority.key != &GAME_AUTHORITY {
        msg!("Invalid Solamon game authority");
        return Err(ProgramError::InvalidAccountData);
    }
    if !game_authority.is_signer {
        msg!("Solamon game authority signature is required");
        return Err(ProgramError::MissingRequiredSignature);
    }
    Ok(())
}

fn assert_token_account(
    token_account_info: &AccountInfo,
    mint: &Pubkey,
    owner: Option<&Pubkey>,
    minimum_amount: Option<u64>,
) -> ProgramResult {
    if token_account_info.owner != &SPL_TOKEN_PROGRAM_ID {
        return Err(ProgramError::IncorrectProgramId);
    }

    let data = token_account_info.data.borrow();
    if data.len() < TOKEN_ACCOUNT_MIN_LEN {
        return Err(ProgramError::InvalidAccountData);
    }
    let token_mint = Pubkey::new_from_array(get_array_at::<PUBKEY_LEN>(
        &data,
        TOKEN_ACCOUNT_MINT_OFFSET,
    ));
    if token_mint != *mint {
        return Err(ProgramError::InvalidAccountData);
    }
    if let Some(expected_owner) = owner {
        let token_owner = Pubkey::new_from_array(get_array_at::<PUBKEY_LEN>(
            &data,
            TOKEN_ACCOUNT_OWNER_OFFSET,
        ));
        if token_owner != *expected_owner {
            return Err(ProgramError::InvalidAccountData);
        }
    }
    if let Some(required_amount) = minimum_amount {
        let amount = u64::from_le_bytes(get_array_at::<8>(
            &data,
            TOKEN_ACCOUNT_AMOUNT_OFFSET,
        ));
        if amount < required_amount {
            return Err(ProgramError::InsufficientFunds);
        }
    }
    Ok(())
}

fn create_pda_if_empty<'a>(
    payer: &AccountInfo<'a>,
    account: &AccountInfo<'a>,
    system_program: &AccountInfo<'a>,
    space: usize,
    program_id: &Pubkey,
    signer_seeds: &[&[u8]],
) -> ProgramResult {
    if !account.data_is_empty() {
        return Ok(());
    }
    let rent = Rent::get()?;
    let lamports = rent.minimum_balance(space);
    invoke_signed(
        &system_instruction::create_account(
            payer.key,
            account.key,
            lamports,
            space as u64,
            program_id,
        ),
        &[payer.clone(), account.clone(), system_program.clone()],
        &[signer_seeds],
    )
}

#[derive(Clone, Copy)]
pub struct PlayerState {
    pub initialized: bool,
    pub version: u8,
    pub owner: Pubkey,
    pub character_mint: Pubkey,
    pub name: [u8; NAME_LEN],
    pub avatar: [u8; AVATAR_LEN],
    pub save_version: u64,
    pub save_hash: Pubkey,
    pub save_uri: [u8; SAVE_URI_LEN],
    pub map_id: [u8; MAP_ID_LEN],
    pub tile_x: u16,
    pub tile_y: u16,
    pub saved_at_slot: u64,
    pub party_len: u8,
    pub party: [Pubkey; MAX_PARTY],
}

impl Default for PlayerState {
    fn default() -> Self {
        Self {
            initialized: false,
            version: PLAYER_STATE_VERSION,
            owner: Pubkey::default(),
            character_mint: Pubkey::default(),
            name: [0; NAME_LEN],
            avatar: [0; AVATAR_LEN],
            save_version: 0,
            save_hash: Pubkey::default(),
            save_uri: [0; SAVE_URI_LEN],
            map_id: [0; MAP_ID_LEN],
            tile_x: 0,
            tile_y: 0,
            saved_at_slot: 0,
            party_len: 0,
            party: [Pubkey::default(); MAX_PARTY],
        }
    }
}

impl PlayerState {
    pub fn pack(&self, dst: &mut [u8]) -> ProgramResult {
        if dst.len() < PLAYER_STATE_SPACE {
            return Err(ProgramError::AccountDataTooSmall);
        }
        let mut offset = 0;
        dst[offset] = self.initialized as u8;
        offset += 1;
        dst[offset] = self.version;
        offset += 1;
        put_pubkey(dst, &mut offset, &self.owner);
        put_pubkey(dst, &mut offset, &self.character_mint);
        put_bytes(dst, &mut offset, &self.name);
        put_bytes(dst, &mut offset, &self.avatar);
        put_u64(dst, &mut offset, self.save_version);
        put_pubkey(dst, &mut offset, &self.save_hash);
        put_bytes(dst, &mut offset, &self.save_uri);
        put_bytes(dst, &mut offset, &self.map_id);
        put_u16(dst, &mut offset, self.tile_x);
        put_u16(dst, &mut offset, self.tile_y);
        put_u64(dst, &mut offset, self.saved_at_slot);
        dst[offset] = self.party_len;
        offset += 1;
        for key in self.party {
            put_pubkey(dst, &mut offset, &key);
        }
        Ok(())
    }

    pub fn unpack(src: &[u8]) -> Result<Self, ProgramError> {
        if src.len() < PLAYER_STATE_SPACE {
            return Err(ProgramError::AccountDataTooSmall);
        }
        let mut offset = 0;
        let initialized = src[offset] != 0;
        offset += 1;
        let version = src[offset];
        offset += 1;
        let owner = get_pubkey(src, &mut offset);
        let character_mint = get_pubkey(src, &mut offset);
        let name = get_array::<NAME_LEN>(src, &mut offset);
        let avatar = get_array::<AVATAR_LEN>(src, &mut offset);
        let save_version = get_u64(src, &mut offset);
        let save_hash = get_pubkey(src, &mut offset);
        let save_uri = get_array::<SAVE_URI_LEN>(src, &mut offset);
        let map_id = get_array::<MAP_ID_LEN>(src, &mut offset);
        let tile_x = get_u16(src, &mut offset);
        let tile_y = get_u16(src, &mut offset);
        let saved_at_slot = get_u64(src, &mut offset);
        let party_len = src[offset];
        offset += 1;
        let mut party = [Pubkey::default(); MAX_PARTY];
        for slot in party.iter_mut() {
            *slot = get_pubkey(src, &mut offset);
        }
        Ok(Self {
            initialized,
            version,
            owner,
            character_mint,
            name,
            avatar,
            save_version,
            save_hash,
            save_uri,
            map_id,
            tile_x,
            tile_y,
            saved_at_slot,
            party_len,
            party,
        })
    }
}

#[derive(Clone, Copy)]
pub struct SaveBlobManifest {
    pub initialized: bool,
    pub version: u8,
    pub owner: Pubkey,
    pub character_mint: Pubkey,
    pub save_hash: Pubkey,
    pub compression: u8,
    pub total_len: u32,
    pub chunk_size: u16,
    pub chunk_count: u16,
}

impl SaveBlobManifest {
    pub fn pack(&self, dst: &mut [u8]) -> ProgramResult {
        if dst.len() < SAVE_BLOB_MANIFEST_SPACE {
            return Err(ProgramError::AccountDataTooSmall);
        }
        let mut offset = 0;
        dst[offset] = self.initialized as u8;
        offset += 1;
        dst[offset] = self.version;
        offset += 1;
        put_pubkey(dst, &mut offset, &self.owner);
        put_pubkey(dst, &mut offset, &self.character_mint);
        put_pubkey(dst, &mut offset, &self.save_hash);
        dst[offset] = self.compression;
        offset += 1;
        put_u32(dst, &mut offset, self.total_len);
        put_u16(dst, &mut offset, self.chunk_size);
        put_u16(dst, &mut offset, self.chunk_count);
        Ok(())
    }
}

#[derive(Clone, Copy)]
pub struct SaveBlobChunk {
    pub initialized: bool,
    pub version: u8,
    pub owner: Pubkey,
    pub character_mint: Pubkey,
    pub save_hash: Pubkey,
    pub index: u16,
    pub len: u16,
    pub bytes: [u8; MAX_SAVE_BLOB_CHUNK_BYTES],
}

impl SaveBlobChunk {
    pub fn pack(&self, dst: &mut [u8]) -> ProgramResult {
        if dst.len() < SAVE_BLOB_CHUNK_SPACE {
            return Err(ProgramError::AccountDataTooSmall);
        }
        let mut offset = 0;
        dst[offset] = self.initialized as u8;
        offset += 1;
        dst[offset] = self.version;
        offset += 1;
        put_pubkey(dst, &mut offset, &self.owner);
        put_pubkey(dst, &mut offset, &self.character_mint);
        put_pubkey(dst, &mut offset, &self.save_hash);
        put_u16(dst, &mut offset, self.index);
        put_u16(dst, &mut offset, self.len);
        put_bytes(dst, &mut offset, &self.bytes);
        Ok(())
    }
}

struct Cursor<'a> {
    data: &'a [u8],
    offset: usize,
}

impl<'a> Cursor<'a> {
    fn new(data: &'a [u8]) -> Self {
        Self { data, offset: 0 }
    }

    fn read_u8(&mut self) -> Result<u8, ProgramError> {
        if self.offset >= self.data.len() {
            return Err(ProgramError::InvalidInstructionData);
        }
        let value = self.data[self.offset];
        self.offset += 1;
        Ok(value)
    }

    fn read_u16(&mut self) -> Result<u16, ProgramError> {
        let bytes = self.read_array::<2>()?;
        Ok(u16::from_le_bytes(bytes))
    }

    fn read_u32(&mut self) -> Result<u32, ProgramError> {
        let bytes = self.read_array::<4>()?;
        Ok(u32::from_le_bytes(bytes))
    }

    fn read_u64(&mut self) -> Result<u64, ProgramError> {
        let bytes = self.read_array::<8>()?;
        Ok(u64::from_le_bytes(bytes))
    }

    fn read_pubkey_bytes(&mut self) -> Result<[u8; PUBKEY_LEN], ProgramError> {
        self.read_array::<PUBKEY_LEN>()
    }

    fn read_fixed_string(&mut self, max_len: usize) -> Result<String, ProgramError> {
        let len = self.read_u8()? as usize;
        if len > max_len || self.offset + len > self.data.len() {
            return Err(ProgramError::InvalidInstructionData);
        }
        let text = core::str::from_utf8(&self.data[self.offset..self.offset + len])
            .map_err(|_| ProgramError::InvalidInstructionData)?;
        self.offset += len;
        Ok(text.to_string())
    }

    fn read_bytes(&mut self, len: usize) -> Result<&'a [u8], ProgramError> {
        if self.offset + len > self.data.len() {
            return Err(ProgramError::InvalidInstructionData);
        }
        let out = &self.data[self.offset..self.offset + len];
        self.offset += len;
        Ok(out)
    }

    fn read_array<const N: usize>(&mut self) -> Result<[u8; N], ProgramError> {
        if self.offset + N > self.data.len() {
            return Err(ProgramError::InvalidInstructionData);
        }
        let mut out = [0u8; N];
        out.copy_from_slice(&self.data[self.offset..self.offset + N]);
        self.offset += N;
        Ok(out)
    }
}

fn write_bytes(dst: &mut [u8], src: &[u8]) {
    dst.fill(0);
    let len = dst.len().min(src.len());
    dst[..len].copy_from_slice(&src[..len]);
}

fn put_bytes(dst: &mut [u8], offset: &mut usize, src: &[u8]) {
    dst[*offset..*offset + src.len()].copy_from_slice(src);
    *offset += src.len();
}

fn put_pubkey(dst: &mut [u8], offset: &mut usize, key: &Pubkey) {
    put_bytes(dst, offset, key.as_ref());
}

fn put_u16(dst: &mut [u8], offset: &mut usize, value: u16) {
    put_bytes(dst, offset, &value.to_le_bytes());
}

fn put_u32(dst: &mut [u8], offset: &mut usize, value: u32) {
    put_bytes(dst, offset, &value.to_le_bytes());
}

fn put_u64(dst: &mut [u8], offset: &mut usize, value: u64) {
    put_bytes(dst, offset, &value.to_le_bytes());
}

fn get_array<const N: usize>(src: &[u8], offset: &mut usize) -> [u8; N] {
    let mut out = [0u8; N];
    out.copy_from_slice(&src[*offset..*offset + N]);
    *offset += N;
    out
}

fn get_array_at<const N: usize>(src: &[u8], offset: usize) -> [u8; N] {
    let mut out = [0u8; N];
    out.copy_from_slice(&src[offset..offset + N]);
    out
}

fn get_pubkey(src: &[u8], offset: &mut usize) -> Pubkey {
    Pubkey::new_from_array(get_array::<PUBKEY_LEN>(src, offset))
}

fn get_u16(src: &[u8], offset: &mut usize) -> u16 {
    u16::from_le_bytes(get_array::<2>(src, offset))
}

fn get_u64(src: &[u8], offset: &mut usize) -> u64 {
    u64::from_le_bytes(get_array::<8>(src, offset))
}
