from ape import Project
from ape.contracts.base import ContractContainer
from ape_starknet.contracts import StarknetContract
from starknet_py.utils.typed_data import TypedData
from starknet_py.cairo.felt import encode_shortstring
from eth_utils import to_hex
from ape_starknet.utils import to_int


def test_declare_then_deploy(project_hello_starknet, account, chain, provider):
    """
    This test shows a realistic flow.

    1. Declare a contract.
    2. Declare a factory contract.
    3. Use the universal deployer contract to deploy factory contract
       (with other class hash as argument).
    4. Use the factory contract to deploy the other contract.
    """
    balance_before = account.balance

    project = Project(project_hello_starknet)
    hello_starknet = project.get_contract("hello_starknet")
    decleration = account.declare(hello_starknet)

    assert decleration.class_hash

    actual_balance = account.balance
    total_fees = decleration.total_fees_paid
    expected_balance = balance_before - total_fees
    assert actual_balance == expected_balance

    deployed_contract = account.deploy(hello_starknet)

    contract_instance = StarknetContract.from_address(
        address=deployed_contract.address, provider=account
    )

    x = contract_instance.get_balance(auto_estimate=True)


def test_check_signature(account, provider):
    # Test with a SignableMessage
    structured_data = {
        "types": {
            "StarkNetDomain": [
                {"name": "name", "type": "felt"},
                {"name": "version", "type": "felt"},
                {"name": "chainId", "type": "felt"},
            ],
            "Message": [
                {"name": "message", "type": "felt"},
            ],
        },
        "primaryType": "Message",
        "domain": {
            "name": "test",
            "version": "1",
            "chainId": to_hex(provider.chain_id),
        },
        "message": {
            "message": encode_shortstring("Hello, Starknet!"),
        },
    }
    typed_data = TypedData.from_dict(structured_data)

    # Calculate the message hash
    message_hash = typed_data.message_hash(int(account.address, 16))

    # Sign the message
    signature = account.sign_message(message_hash)

    # Check the signature
    assert account.check_signature(message_hash, signature)

    # Test with a string message
    message_str = "Hello, Starknet!"
    message_hash = encode_shortstring(message_str)
    signature = account.sign_message(message_hash)

    assert account.check_signature(message_hash, signature)

    # Test with bytes
    message_bytes = b"Hello, Starknet!"
    message_hash = int.from_bytes(message_bytes, "big")
    signature = account.sign_message(message_hash)

    assert account.check_signature(message_hash, signature)

    # Test with invalid signature
    invalid_signature = (12345, 67890)
    assert not account.check_signature(message_hash, invalid_signature)


def test_declare_then_deploy2(project_erc20_starknet, account, chain, provider):
    """
    This test shows a realistic flow.

    1. Declare a contract.
    2. Declare a factory contract.
    3. Use the universal deployer contract to deploy factory contract
       (with other class hash as argument).
    4. Use the factory contract to deploy the other contract.
    """
    balance_before = account.balance

    project = Project(project_erc20_starknet)
    print(project.contracts)
    my_token = project.get_contract("MyERC20Token")
    contract_type = my_token.contract_type if isinstance(my_token, ContractContainer) else my_token
    assert contract_type is not None

    decleration = account.declare(my_token)

    assert decleration.class_hash

    actual_balance = account.balance
    total_fees = decleration.total_fees_paid
    expected_balance = balance_before - total_fees
    assert actual_balance == expected_balance

    from ape.contracts.base import ContractInstance

    my_token_deploy: ContractInstance = account.deploy(
        my_token, "ERC", "ERC", 100000, to_int(account.address), publish=False
    )
    contract = my_token.at(
        address=my_token_deploy.address,
        txn_hash=my_token_deploy.txn_hash,
    )
    assert contract is not None
