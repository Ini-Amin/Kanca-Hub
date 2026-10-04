-- ============================================================
-- temp-mail-api schema
-- Stores temp addresses and the mail that arrives for them.
-- ============================================================

-- Addresses created via ?action=create
create table if not exists public.temp_addresses (
    id            bigint generated always as identity primary key,
    address       text not null unique,
    owner_token   text not null unique,
    domain        text not null,
    created_at    timestamptz not null default now()
);

create index if not exists temp_addresses_address_idx     on public.temp_addresses (address);
create index if not exists temp_addresses_owner_token_idx on public.temp_addresses (owner_token);

-- Mail ingested from the Cloudflare Worker
create table if not exists public.temp_messages (
    id            bigint generated always as identity primary key,
    message_id    text not null unique,
    address       text not null,
    from_address  text,
    from_name     text,
    subject       text,
    text_body     text,
    html_body     text,
    raw           jsonb,
    received_at   timestamptz not null default now()
);

create index if not exists temp_messages_address_idx     on public.temp_messages (address);
create index if not exists temp_messages_received_at_idx on public.temp_messages (received_at desc);

-- Only the service_role (used by the Edge Function) touches these tables.
-- RLS on with no policies => anon/public cannot read or write.
alter table public.temp_addresses enable row level security;
alter table public.temp_messages  enable row level security;
