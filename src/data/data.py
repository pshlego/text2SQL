import abc
import attrs
import torch
import warnings

from typing import List, Any
from torch.utils.data import Dataset
from src.parser.parser import BadPostgresSyntax
from src.data.schema import Schema
import src.utils.tensor as tensor_utils
import src.preprocess.relation_extractor as relation_extractor
@attrs.define
class TextToSQLDatum:
    schema: str
    sql: str
    parse_tree: Any
    nl: str
    nl_tokens: List[str]
    nl_tensor: torch.Tensor
    tokenizer: Any


    @property
    def input_tensor(self):
        # Get sep token
        sep_tok_id = self.tokenizer.convert_tokens_to_ids(self.tokenizer.sep_token)
        sep_tok_tensor = torch.tensor([sep_tok_id])
        return torch.cat([self.nl_tensor, sep_tok_tensor, self.schema.tensor_repr])

    @property
    def relation_matrix(self):
        # Generate block matrices
        tok_tok_rel_matrix = torch.from_numpy(relation_extractor.get_relation_matrix_for_tokens(self.nl_tokens))
        tok_sch_rel_matrix = torch.from_numpy(relation_extractor.get_relation_matrix_for_tokens_schema(self.nl_tokens, self.schema.tables_and_columns))
        sch_tok_rel_matrix = relation_extractor.inverse_matrix(tok_sch_rel_matrix)
        sch_sch_rel_matrix = self.schema.relation_matrix
        # Get length
        token_len = len(tok_tok_rel_matrix)
        full_input_len = len(self.nl_tokens) + len(self.schema.tables_and_columns)
        # Combine block matrices into relation matrix of the whole input
        combined_matrix = torch.zeros((full_input_len, full_input_len), dtype=torch.int16)
        combined_matrix[:token_len, :token_len] = tok_tok_rel_matrix
        combined_matrix[:token_len, token_len:] = tok_sch_rel_matrix
        combined_matrix[token_len:, :token_len] = sch_tok_rel_matrix
        combined_matrix[token_len:, token_len:] = sch_sch_rel_matrix
        return self.schema.relation_matrix


@attrs.define
class TextToSQLBatch:
    data: List[TextToSQLDatum]
    input_tensor: torch.Tensor
    relation_matrix: torch.Tensor
    
    def __getitem__(self, index):
        return self.data[index]
    
    def __len__(self):
        return len(self.data)


@attrs.define
class TextToSQLDataset(Dataset):
    def __init__(self, file_paths, tokenizer, sql_parser, data_filter_func=None):
        super(TextToSQLDataset, self).__init__()
        self.file_paths: str = file_paths
        self.tokenizer: Any = tokenizer
        self.sql_parser: Any = sql_parser
        self.data_filter_func: Any = data_filter_func
        self.data: List[TextToSQLDatum] = []
        self.apply()

    def __getitem__(self, index):
        return self.data[index]

    def __len__(self) -> int:
        return len(self.data)
    
    def _check_and_warn_on_missing_schema_cache(self):
        assert len(Schema.schema_cache), "schemaGenerator.schema_cache is empty. Schema information must be cached first before loading the dataset."
    
    def apply(self):
        raw_data = self._read_in_data_from_files(self.file_paths)
        filtered_data = list(filter(self.data_filter_func, raw_data)) if self.data_filter_func else raw_data
        self._check_and_warn_on_missing_schema_cache()
        for datum in filtered_data:
            # Pass item that has query with a bad Postgres syntax
            try:
                self.data.append(self._raw_datum_to_textToSQLDatum(datum))
            except Exception as e:
                if type(e) == BadPostgresSyntax:
                    warnings.warn(f"Error in converting raw datum to TextToSQLDatum. Bad postgresql syntax for query:{e.args[0]}")
                else:
                    raise e
        return self

    @abc.abstractclassmethod
    def _read_in_data_from_files(self, file_paths: List[str]) -> List[TextToSQLDatum]:
        pass

    @abc.abstractclassmethod
    def _raw_datum_to_textToSQLDatum(self, raw_data: TextToSQLDatum) -> TextToSQLDatum:
        pass


def collate_fn(item_list):
    
    input_tensor = tensor_utils.zero_pad_batching([item.nl_tensor for item in item_list])
    relation_matrix = tensor_utils.zero_pad_batching([item.relation_matrix for item in item_list])
    return TextToSQLBatch(item_list, input_tensor, relation_matrix)
